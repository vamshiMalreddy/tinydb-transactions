"""
Hidden tests for the TinyDB transactions feature.

These tests only use the public API described in instruction.md plus the
standard TinyDB storage/middleware extension points.
"""
import copy
import os

import pytest

from tinydb import Query, TinyDB, TransactionError, where
from tinydb.middlewares import CachingMiddleware, Middleware
from tinydb.storages import JSONStorage, MemoryStorage, Storage
from tinydb.table import Document


class Boom(Exception):
    pass


class CountingMiddleware(Middleware):
    """Counts calls to ``write`` on the wrapped storage."""

    def __init__(self, storage_cls):
        super().__init__(storage_cls)
        self.writes = 0

    def read(self):
        return self.storage.read()

    def write(self, data):
        self.writes += 1
        self.storage.write(data)

    def close(self):
        self.storage.close()


class DictStorage(Storage):
    """A minimal custom storage that returns its live internal object."""

    def __init__(self):
        self.data = None
        self.writes = 0

    def read(self):
        return self.data

    def write(self, data):
        self.writes += 1
        self.data = data


@pytest.fixture(params=['memory', 'json', 'caching', 'custom'])
def db(request, tmp_path):
    if request.param == 'memory':
        db_ = TinyDB(storage=CountingMiddleware(MemoryStorage))
    elif request.param == 'json':
        db_ = TinyDB(str(tmp_path / 'db.json'),
                     storage=CountingMiddleware(JSONStorage))
    elif request.param == 'caching':
        db_ = TinyDB(storage=CountingMiddleware(
            CachingMiddleware(MemoryStorage)))
    else:
        db_ = TinyDB(storage=DictStorage)
    yield db_
    try:
        db_.close()
    except Exception:
        pass


def writes(db_):
    return db_.storage.writes


def state(db_):
    """A deep copy of what the storage currently holds."""
    return copy.deepcopy(db_.storage.read())


def seed(db_):
    db_.insert_multiple([{'n': 1}, {'n': 2}, {'n': 3}])
    db_.table('other').insert({'x': 'a'})


# ---------------------------------------------------------------------------
# API surface
# ---------------------------------------------------------------------------

def test_transaction_error_is_exception():
    assert issubclass(TransactionError, Exception)


def test_enter_returns_transaction_and_flags(db):
    assert db.in_transaction is False
    txn = db.transaction()
    with txn as entered:
        assert entered is txn
        assert txn.active is True
        assert db.in_transaction is True
    assert txn.active is False
    assert db.in_transaction is False


def test_transaction_returns_new_object_each_time(db):
    assert db.transaction() is not db.transaction()


# ---------------------------------------------------------------------------
# Commit
# ---------------------------------------------------------------------------

def test_commit_applies_all_changes(db):
    seed(db)
    with db.transaction():
        db.insert({'n': 4})
        db.update({'n': 20}, where('n') == 2)
        db.remove(where('n') == 1)
        db.table('other').insert({'x': 'b'})
        db.table('brand_new').insert({'y': 1})

    assert sorted(d['n'] for d in db.all()) == [3, 4, 20]
    assert len(db.table('other')) == 2
    assert db.table('brand_new').all() == [{'y': 1}]
    assert {'_default', 'other', 'brand_new'} <= set(state(db))


def test_single_write_on_commit_and_none_during(db):
    seed(db)
    before = writes(db)
    with db.transaction():
        db.insert({'n': 4})
        db.insert_multiple([{'n': 5}, {'n': 6}])
        db.table('other').update({'x': 'z'})
        db.upsert({'n': 7}, where('n') == 99)
        assert writes(db) == before
    assert writes(db) == before + 1


def test_read_only_transaction_does_not_write(db):
    seed(db)
    before = writes(db)
    with db.transaction():
        db.search(where('n') > 1)
        db.all()
        len(db)
        db.tables()
    assert writes(db) == before


def test_empty_transaction_does_not_write(db):
    before = writes(db)
    with db.transaction():
        pass
    assert writes(db) == before


def test_committed_data_is_persisted_to_json(tmp_path):
    path = str(tmp_path / 'db.json')
    with TinyDB(path) as db_:
        with db_.transaction():
            db_.insert({'a': 1})
            db_.table('t').insert({'b': 2})
    with TinyDB(path) as db_:
        assert db_.all() == [{'a': 1}]
        assert db_.table('t').all() == [{'b': 2}]


# ---------------------------------------------------------------------------
# Rollback on exception
# ---------------------------------------------------------------------------

def test_exception_discards_everything(db):
    seed(db)
    before_state = state(db)
    before_writes = writes(db)
    other = db.table('other')

    with pytest.raises(Boom):
        with db.transaction():
            db.insert({'n': 4})
            db.update({'n': 100}, where('n') == 1)
            db.update(lambda d: d.__setitem__('n', -1), where('n') == 2)
            db.remove(where('n') == 3)
            other.insert({'x': 'b'})
            db.table('created_inside').insert({'q': 1})
            raise Boom()

    assert writes(db) == before_writes
    assert state(db) == before_state
    assert sorted(d['n'] for d in db.all()) == [1, 2, 3]
    assert other.all() == [{'x': 'a'}]
    assert 'created_inside' not in db.tables()
    assert db.table('created_inside').all() == []


def test_original_exception_propagates_unchanged(db):
    err = Boom('specific')
    with pytest.raises(Boom) as info:
        with db.transaction():
            db.insert({'a': 1})
            raise err
    assert info.value is err


def test_storage_untouched_during_transaction(db):
    seed(db)
    before_state = state(db)
    with db.transaction():
        db.insert({'n': 4})
        db.update(lambda d: d.__setitem__('n', 0))
        db.table('other').truncate()
        db.drop_table('other')
        assert state(db) == before_state
    assert state(db) != before_state


def test_rollback_on_empty_database_leaves_it_empty(db):
    assert db.storage.read() is None
    with pytest.raises(Boom):
        with db.transaction():
            db.insert({'a': 1})
            raise Boom()
    assert db.storage.read() is None
    assert writes(db) == 0
    assert db.tables() == set()


def test_rollback_keeps_json_file_empty(tmp_path):
    path = tmp_path / 'db.json'
    db_ = TinyDB(str(path))
    with pytest.raises(Boom):
        with db_.transaction():
            db_.insert({'a': 1})
            raise Boom()
    assert os.path.getsize(path) == 0
    db_.close()


# ---------------------------------------------------------------------------
# Read your own writes
# ---------------------------------------------------------------------------

def test_reads_inside_transaction_see_pending_changes(db):
    seed(db)
    with db.transaction():
        doc_id = db.insert({'n': 10})
        db.remove(where('n') == 1)
        db.table('fresh').insert({'z': 1})

        assert len(db) == 3
        assert db.count(where('n') >= 2) == 3
        assert db.contains(where('n') == 10)
        assert not db.contains(where('n') == 1)
        assert db.get(doc_id=doc_id) == {'n': 10}
        assert db.get(where('n') == 10).doc_id == doc_id
        assert sorted(d['n'] for d in db) == [2, 3, 10]
        assert sorted(d['n'] for d in db.all()) == [2, 3, 10]
        assert sorted(d['n'] for d in db.search(where('n') > 0)) == [2, 3, 10]
        assert 'fresh' in db.tables()


def test_drop_tables_inside_transaction_is_visible_then_rolled_back(db):
    seed(db)
    before_state = state(db)
    with pytest.raises(Boom):
        with db.transaction():
            db.drop_tables()
            assert db.tables() == set()
            assert len(db) == 0
            raise Boom()
    assert state(db) == before_state
    assert db.tables() == {'_default', 'other'}
    assert len(db.table('other')) == 1


def test_drop_table_and_truncate_rolled_back(db):
    seed(db)
    other = db.table('other')
    with pytest.raises(Boom):
        with db.transaction():
            db.drop_table('other')
            db.truncate()
            assert 'other' not in db.tables()
            assert len(db) == 0
            raise Boom()
    assert 'other' in db.tables()
    assert other.all() == [{'x': 'a'}]
    assert db.table('other').all() == [{'x': 'a'}]
    assert len(db) == 3


def test_persist_empty_table_is_transactional(db):
    seed(db)
    before_state = state(db)
    with pytest.raises(Boom):
        with db.transaction():
            db.table('persisted', persist_empty=True)
            assert 'persisted' in db.tables()
            raise Boom()
    assert 'persisted' not in db.tables()
    assert state(db) == before_state


# ---------------------------------------------------------------------------
# Query cache and document IDs
# ---------------------------------------------------------------------------

def test_query_cache_not_stale_after_rollback(db):
    seed(db)
    q = Query().n >= 1
    assert len(db.search(q)) == 3          # populate cache
    with pytest.raises(Boom):
        with db.transaction():
            db.insert({'n': 50})
            assert len(db.search(q)) == 4  # cache inside txn
            raise Boom()
    assert len(db.search(q)) == 3
    assert db.count(q) == 3


def test_query_cache_after_explicit_rollback(db):
    seed(db)
    q = Query().n == 2
    with db.transaction() as txn:
        db.update({'n': 2}, where('n') == 1)
        assert len(db.search(q)) == 2
        txn.rollback()
        assert len(db.search(q)) == 1
    assert len(db.search(q)) == 1


def test_doc_ids_restored_after_rollback(db):
    seed(db)
    with pytest.raises(Boom):
        with db.transaction():
            db.insert({'n': 4})
            db.insert({'n': 5})
            raise Boom()
    assert db.insert({'n': 4}) == 4


def test_doc_id_counter_ahead_of_max_id_is_preserved(db):
    # Upstream TinyDB never re-uses the ID of the most recently removed
    # document while the same Table object is in use.
    seed(db)
    db.remove(doc_ids=[3])
    with pytest.raises(Boom):
        with db.transaction():
            db.insert({'n': 'discarded'})
            raise Boom()
    assert db.insert({'n': 'kept'}) == 4


def test_doc_ids_restored_for_table_created_inside(db):
    with pytest.raises(Boom):
        with db.transaction():
            t = db.table('inner')
            t.insert({'a': 1})
            t.insert({'a': 2})
            raise Boom()
    assert t.insert({'a': 3}) == 1
    assert db.table('inner').all() == [{'a': 3}]


def test_doc_ids_restored_for_held_table_after_drop(db):
    seed(db)
    other = db.table('other')
    with pytest.raises(Boom):
        with db.transaction():
            db.drop_tables()
            other.insert({'x': 'new'})
            raise Boom()
    assert other.insert({'x': 'b'}) == 2


def test_doc_ids_continue_after_commit(db):
    seed(db)
    with db.transaction():
        assert db.insert({'n': 4}) == 4
    assert db.insert({'n': 5}) == 5


def test_document_with_explicit_id_inside_rolled_back_transaction(db):
    seed(db)
    with pytest.raises(Boom):
        with db.transaction():
            db.insert(Document({'n': 100}, doc_id=100))
            raise Boom()
    assert db.get(doc_id=100) is None
    assert db.insert({'n': 4}) == 4


# ---------------------------------------------------------------------------
# Nesting
# ---------------------------------------------------------------------------

def test_nested_inner_failure_only_discards_inner(db):
    seed(db)
    before = writes(db)
    with db.transaction():
        db.insert({'n': 'outer-1'})
        with pytest.raises(Boom):
            with db.transaction():
                db.insert({'n': 'inner'})
                db.table('other').truncate()
                raise Boom()
        assert db.count(where('n') == 'inner') == 0
        assert len(db.table('other')) == 1
        db.insert({'n': 'outer-2'})
        assert writes(db) == before
    assert writes(db) == before + 1
    assert [d['n'] for d in db.all()] == [1, 2, 3, 'outer-1', 'outer-2']
    assert [d.doc_id for d in db.all()] == [1, 2, 3, 4, 5]
    assert len(db.table('other')) == 1


def test_nested_commit_then_outer_failure_discards_all(db):
    seed(db)
    before_state = state(db)
    with pytest.raises(Boom):
        with db.transaction():
            db.insert({'n': 'outer'})
            with db.transaction():
                db.insert({'n': 'inner'})
            assert db.count(where('n') == 'inner') == 1
            raise Boom()
    assert state(db) == before_state
    assert db.insert({'n': 4}) == 4


def test_nested_commit_does_not_write(db):
    seed(db)
    before = writes(db)
    with db.transaction():
        with db.transaction():
            db.insert({'n': 4})
        assert writes(db) == before
        assert db.in_transaction
    assert writes(db) == before + 1


def test_deeply_nested_savepoints(db):
    with db.transaction():
        db.insert({'lvl': 1})
        with db.transaction():
            db.insert({'lvl': 2})
            with pytest.raises(Boom):
                with db.transaction():
                    db.insert({'lvl': 3})
                    raise Boom()
            with db.transaction():
                db.insert({'lvl': '3b'})
    assert [d['lvl'] for d in db.all()] == [1, 2, '3b']
    assert [d.doc_id for d in db.all()] == [1, 2, 3]


def test_only_inner_writes_rolled_back_means_no_write(db):
    seed(db)
    before = writes(db)
    with db.transaction():
        db.all()
        with pytest.raises(Boom):
            with db.transaction():
                db.insert({'n': 4})
                raise Boom()
    assert writes(db) == before


# ---------------------------------------------------------------------------
# Explicit rollback
# ---------------------------------------------------------------------------

def test_explicit_rollback_then_autocommit(db):
    seed(db)
    before = writes(db)
    with db.transaction() as txn:
        db.insert({'n': 'gone'})
        txn.rollback()
        assert txn.active is False
        assert db.in_transaction is False
        assert db.count(where('n') == 'gone') == 0
        db.insert({'n': 'kept'})
        assert writes(db) == before + 1
    assert writes(db) == before + 1
    assert [d['n'] for d in db.all()] == [1, 2, 3, 'kept']
    assert db.get(where('n') == 'kept').doc_id == 4


def test_explicit_rollback_in_nested_moves_ops_to_outer(db):
    seed(db)
    before_state = state(db)
    with pytest.raises(Boom):
        with db.transaction():
            with db.transaction() as inner:
                db.insert({'n': 'a'})
                inner.rollback()
                assert db.in_transaction is True
                db.insert({'n': 'b'})  # now part of the outer transaction
            assert db.count(where('n') == 'b') == 1
            raise Boom()
    assert state(db) == before_state


def test_explicit_rollback_discards_committed_nested(db):
    seed(db)
    with db.transaction() as outer:
        with db.transaction():
            db.insert({'n': 'nested'})
        outer.rollback()
    assert db.count(where('n') == 'nested') == 0
    assert len(db) == 3


def test_rollback_twice_raises(db):
    with db.transaction() as txn:
        txn.rollback()
        with pytest.raises(TransactionError):
            txn.rollback()


def test_rollback_after_commit_raises(db):
    with db.transaction() as txn:
        db.insert({'a': 1})
    with pytest.raises(TransactionError):
        txn.rollback()
    assert db.all() == [{'a': 1}]


def test_rollback_before_enter_raises(db):
    with pytest.raises(TransactionError):
        db.transaction().rollback()


def test_rollback_outer_while_inner_active_raises(db):
    seed(db)
    with db.transaction() as outer:
        db.insert({'n': 'outer'})
        with db.transaction():
            db.insert({'n': 'inner'})
            with pytest.raises(TransactionError):
                outer.rollback()
            assert outer.active
    assert db.count(where('n') == 'outer') == 1
    assert db.count(where('n') == 'inner') == 1


# ---------------------------------------------------------------------------
# Misuse
# ---------------------------------------------------------------------------

def test_reentering_transaction_raises(db):
    txn = db.transaction()
    with txn:
        db.insert({'a': 1})
    with pytest.raises(TransactionError):
        with txn:
            db.insert({'a': 2})
    assert db.all() == [{'a': 1}]
    assert db.in_transaction is False


def test_reentering_active_transaction_raises(db):
    txn = db.transaction()
    with txn:
        with pytest.raises(TransactionError):
            txn.__enter__()
        assert txn.active
        db.insert({'a': 1})
    assert db.all() == [{'a': 1}]


def test_close_inside_transaction_raises_and_keeps_working(db):
    seed(db)
    with db.transaction() as txn:
        db.insert({'n': 4})
        with pytest.raises(TransactionError):
            db.close()
        assert txn.active
        db.insert({'n': 5})
    assert sorted(d['n'] for d in db.all()) == [1, 2, 3, 4, 5]


# ---------------------------------------------------------------------------
# Compatibility
# ---------------------------------------------------------------------------

def test_storage_property_is_original_object():
    db_ = TinyDB(storage=MemoryStorage)
    assert isinstance(db_.storage, MemoryStorage)
    assert db_.table('t').storage is db_.storage
    with db_.transaction():
        assert db_.storage.__class__ is MemoryStorage
        db_.insert({'a': 1})
    assert db_.storage.read() == {'_default': {'1': {'a': 1}}}


def test_memory_storage_internals_not_mutated(tmp_path):
    db_ = TinyDB(storage=MemoryStorage)
    db_.insert({'a': 1, 'nested': {'k': 1}})
    memory_before = db_.storage.memory
    snapshot = copy.deepcopy(memory_before)
    with pytest.raises(Boom):
        with db_.transaction():
            db_.update({'a': 2})
            db_.update(lambda d: d['nested'].__setitem__('k', 99))
            db_.insert({'b': 1})
            assert db_.storage.memory == snapshot
            raise Boom()
    assert db_.storage.memory == snapshot
    assert db_.all() == [{'a': 1, 'nested': {'k': 1}}]


def test_caching_middleware_flush_after_commit(tmp_path):
    path = str(tmp_path / 'db.json')
    db_ = TinyDB(path, storage=CachingMiddleware(JSONStorage))
    with db_.transaction():
        db_.insert({'a': 1})
        db_.insert({'a': 2})
    db_.storage.flush()
    db_.close()
    with TinyDB(path) as reopened:
        assert reopened.all() == [{'a': 1}, {'a': 2}]


def test_custom_storage_live_object_not_mutated():
    db_ = TinyDB(storage=DictStorage)
    db_.insert({'a': 1})
    live = db_.storage.data
    snapshot = copy.deepcopy(live)
    with pytest.raises(Boom):
        with db_.transaction():
            db_.update({'a': 5})
            db_.table('t').insert({'b': 1})
            raise Boom()
    assert db_.storage.data is live
    assert live == snapshot
    assert db_.storage.writes == 1


def test_behaviour_outside_transactions_unchanged(db):
    seed(db)
    before = writes(db)
    db.insert({'n': 4})
    assert writes(db) == before + 1
    db.update({'n': 40}, where('n') == 4)
    assert writes(db) == before + 2
    assert db.get(where('n') == 40).doc_id == 4
    db.drop_table('other')
    assert 'other' not in db.tables()
    assert writes(db) == before + 3


# ---------------------------------------------------------------------------
# Failures inside transactions
# ---------------------------------------------------------------------------

def _fail_on_second_call(mutate):
    """An update callable that mutates every document it sees in place and
    raises part-way through, after the first document was already changed."""
    calls = []

    def fn(doc):
        calls.append(1)
        mutate(doc)
        if len(calls) == 2:
            raise Boom()
    return fn


def test_failed_update_has_no_partial_effect(db):
    seed(db)
    with db.transaction() as txn:
        db.insert({'n': 4})
        with pytest.raises(Boom):
            db.update(_fail_on_second_call(
                lambda d: d.__setitem__('n', d['n'] * 100)))
        assert txn.active
        assert sorted(d['n'] for d in db.all()) == [1, 2, 3, 4]
        db.insert({'n': 5})
    assert sorted(d['n'] for d in db.all()) == [1, 2, 3, 4, 5]
    assert sorted(d['n'] for d in state(db)['_default'].values()) \
        == [1, 2, 3, 4, 5]


def test_failed_update_nested_values_untouched(db):
    db.insert({'tags': ['a']})
    db.insert({'tags': ['b']})
    db.insert({'tags': ['c']})
    with db.transaction():
        with pytest.raises(Boom):
            db.update(_fail_on_second_call(lambda d: d['tags'].append('x')))
        assert [d['tags'] for d in db.all()] == [['a'], ['b'], ['c']]
    assert [d['tags'] for d in db.all()] == [['a'], ['b'], ['c']]


def test_failed_update_with_condition_in_other_table(db):
    seed(db)
    other = db.table('other')
    other.insert({'x': 'b'})
    with db.transaction():
        db.insert({'n': 4})
        with pytest.raises(Boom):
            other.update(_fail_on_second_call(
                lambda d: d.__setitem__('x', d['x'].upper())),
                where('x').exists())
        assert sorted(d['x'] for d in other.all()) == ['a', 'b']
        assert len(db) == 4
    assert sorted(d['x'] for d in other.all()) == ['a', 'b']
    assert len(db) == 4


def test_failed_update_in_nested_transaction(db):
    seed(db)
    with db.transaction():
        db.update({'n': 10}, where('n') == 1)
        with db.transaction() as inner:
            db.insert({'n': 4})
            with pytest.raises(Boom):
                db.update(_fail_on_second_call(
                    lambda d: d.__setitem__('n', -1)))
            assert inner.active
            assert sorted(d['n'] for d in db.all()) == [2, 3, 4, 10]
        assert sorted(d['n'] for d in db.all()) == [2, 3, 4, 10]
    assert sorted(d['n'] for d in db.all()) == [2, 3, 4, 10]


class FailingMiddleware(Middleware):
    """Raises from ``write`` (or ``read``) on demand, before touching the
    wrapped storage, and counts successful writes."""

    def __init__(self, storage_cls):
        super().__init__(storage_cls)
        self.writes = 0
        self.fail_writes = False
        self.fail_reads = False

    def read(self):
        if self.fail_reads:
            raise OSError('read failed')
        return self.storage.read()

    def write(self, data):
        if self.fail_writes:
            raise OSError('disk full')
        self.writes += 1
        self.storage.write(data)

    def close(self):
        self.storage.close()


class FailingDictStorage(DictStorage):
    def __init__(self):
        super().__init__()
        self.fail_writes = False
        self.fail_reads = False

    def read(self):
        if self.fail_reads:
            raise OSError('read failed')
        return super().read()

    def write(self, data):
        if self.fail_writes:
            raise OSError('disk full')
        super().write(data)


@pytest.fixture(params=['memory', 'json', 'caching', 'custom'])
def fdb(request, tmp_path):
    """A database whose storage can be told to fail. Holds documents 1 and 2;
    document 3 was inserted and removed, so the next ID is 4."""
    if request.param == 'memory':
        db_ = TinyDB(storage=FailingMiddleware(MemoryStorage))
    elif request.param == 'json':
        db_ = TinyDB(str(tmp_path / 'db.json'),
                     storage=FailingMiddleware(JSONStorage))
    elif request.param == 'caching':
        db_ = TinyDB(storage=FailingMiddleware(
            CachingMiddleware(MemoryStorage)))
    else:
        db_ = TinyDB(storage=FailingDictStorage)
    db_.insert_multiple([{'n': 1}, {'n': 2}, {'n': 3}])
    db_.remove(doc_ids=[3])
    yield db_
    db_.storage.fail_writes = False
    db_.storage.fail_reads = False
    try:
        db_.close()
    except Exception:
        pass


def _failed_commit(db_, body):
    txn = db_.transaction()
    with pytest.raises(OSError, match='disk full'):
        with txn:
            body()
            db_.storage.fail_writes = True
    db_.storage.fail_writes = False
    return txn


def test_failed_commit_ends_transaction(fdb):
    txn = _failed_commit(fdb, lambda: fdb.insert({'n': 9}))
    assert txn.active is False
    assert fdb.in_transaction is False
    with pytest.raises(TransactionError):
        txn.rollback()


def test_failed_commit_discards_changes_and_caches(fdb):
    q = where('n') == 9

    def body():
        fdb.insert({'n': 9})
        fdb.update({'n': 20}, where('n') == 2)
        assert fdb.count(q) == 1          # warm the query cache
        assert fdb.contains(where('n') == 20)

    _failed_commit(fdb, body)
    assert fdb.search(q) == []
    assert fdb.count(q) == 0
    assert not fdb.contains(where('n') == 20)
    assert sorted(d['n'] for d in fdb.all()) == [1, 2]


def test_failed_commit_restores_doc_ids(fdb):
    _failed_commit(fdb, lambda: fdb.insert_multiple([{'n': 9}, {'n': 10}]))
    assert fdb.insert({'n': 'next'}) == 4


def test_failed_commit_forgets_new_tables(fdb):
    holder = {}

    def body():
        t = fdb.table('fresh')
        t.insert({'a': 1})
        t.insert({'a': 2})
        fdb.drop_table('_default')
        holder['t'] = t

    _failed_commit(fdb, body)
    assert fdb.tables() == {'_default'}
    assert sorted(d['n'] for d in fdb.all()) == [1, 2]
    t = holder['t']
    assert t.all() == []
    assert t.insert({'a': 3}) == 1


def test_failed_commit_nested_committed_work_discarded(fdb):
    def body():
        with fdb.transaction():
            fdb.insert({'n': 9})
        assert fdb.contains(where('n') == 9)

    _failed_commit(fdb, body)
    assert not fdb.contains(where('n') == 9)
    assert fdb.insert({'n': 'next'}) == 4


def test_retry_after_failed_commit(fdb):
    _failed_commit(fdb, lambda: fdb.insert({'n': 9}))
    before = fdb.storage.writes
    with fdb.transaction():
        assert fdb.insert({'n': 9}) == 4
    assert fdb.storage.writes == before + 1
    assert sorted(d['n'] for d in fdb.all()) == [1, 2, 9]
    fdb.insert({'n': 10})
    assert fdb.storage.writes == before + 2


def test_failed_read_on_enter_starts_no_transaction(fdb):
    txn = fdb.transaction()
    fdb.storage.fail_reads = True
    with pytest.raises(OSError, match='read failed'):
        with txn:
            pass  # pragma: no cover
    fdb.storage.fail_reads = False
    assert txn.active is False
    assert fdb.in_transaction is False
    before = fdb.storage.writes
    fdb.insert({'n': 5})
    assert fdb.storage.writes == before + 1
    with fdb.transaction():
        fdb.insert({'n': 6})
    assert sorted(d['n'] for d in fdb.all()) == [1, 2, 5, 6]
