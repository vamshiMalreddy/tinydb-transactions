The repository in `/app` is TinyDB 4.8.2, a small document database written in Python. It currently has no way to group several write operations so that they either all take effect or none do. Add transaction support.

## Public API

- `TinyDB.transaction()` returns a new transaction object meant to be used as a context manager:

  ```python
  with db.transaction() as txn:
      db.insert({'name': 'alice'})
      db.table('audit').insert({'event': 'created alice'})
  ```

  `__enter__` returns the transaction object itself.
- `TinyDB.in_transaction`: a read-only `bool` property that is `True` while at least one transaction is active on that database.
- A transaction object has:
  - `active`: a read-only `bool` property, `True` from entering the `with` block until the transaction is committed or rolled back.
  - `rollback()`: see below.
- `TransactionError`, a subclass of `Exception`, importable as `from tinydb import TransactionError`.

## Required behaviour

1. **Atomicity.** Every write performed through the database while a transaction is active is part of that transaction. This covers every table, including the default table, tables obtained from `db.table(...)` before or inside the block, and tables created inside the block (including `persist_empty=True`). It covers all write methods: `insert`, `insert_multiple`, `update`, `update_multiple`, `upsert`, `remove`, `truncate`, `drop_table` and `drop_tables`. If the block finishes normally, the changes are committed. If it exits with an exception, every change made in the block is discarded and the original exception propagates unchanged; the transaction never suppresses exceptions.

2. **Storage interaction.**
   - While any transaction is active, the storage's `write()` is never called and the storage contents never change. This includes storages such as `MemoryStorage` that hand out their internal objects from `read()`, so `db.storage.read()` inside a block still returns the pre-transaction data.
   - When the outermost transaction commits, `write()` is called exactly once with the final state. If no write operation ran inside the transaction, `write()` is not called at all.
   - A rollback never calls `write()`. Afterwards the storage holds exactly what it held before, including the "empty database" state, where `read()` returns `None` or a JSON file is still empty.

3. **Read your own writes.** Inside a transaction every read reflects the changes made so far in that transaction. This includes `search`, `get`, `all`, `count`, `contains`, `len()`, iteration and `db.tables()`.

4. **Nesting.** Calling `db.transaction()` while a transaction is active starts a nested transaction (a savepoint).
   - If a nested transaction is rolled back, only the changes made since it began are discarded, and the enclosing transaction carries on.
   - A committed nested transaction does not touch storage; its changes become part of the enclosing transaction and are discarded if the enclosing one is later rolled back.

5. **Explicit rollback.** `txn.rollback()` discards every change made in `txn`, including changes from nested transactions that already committed into it, and ends `txn`.
   - Leaving the `with` block normally afterwards does nothing more.
   - Database operations that follow inside the same block are no longer part of `txn`. They belong to the enclosing transaction, or are written to storage immediately if there is none.
   - `rollback()` raises `TransactionError` if `txn` is not active, or if a transaction nested inside `txn` is still active.

6. **No trace of rolled-back work.** After any rollback, the database behaves exactly as if the discarded operations had never run:
   - query results contain no stale cached data;
   - the next document inserted into a table receives the same document ID it would have received had the discarded operations never run. This also applies to `Table` objects the caller still holds references to, including tables first created inside the discarded transaction.

7. **Failures.**
   - If a write method raises an exception while a transaction is active (for example, a function passed to `update` raises after it has already modified some documents), that call has no effect: the data seen by the transaction is exactly what it was before the call. The exception propagates unchanged and the transaction stays active and usable.
   - If the storage's `write()` raises while the outermost transaction is committing, the exception propagates unchanged and the transaction ends as if it had been rolled back: it is no longer active, `in_transaction` is `False`, and the database behaves as described in item 6, as if none of the transaction's operations had run. What the failed `write()` call itself left in the storage is up to the storage and is not checked.
   - If the storage's `read()` raises when a transaction is entered, the exception propagates and no transaction is started.

8. **Misuse.**
   - Entering a transaction object that has already been entered raises `TransactionError`.
   - `db.close()` while a transaction is active raises `TransactionError`; the database stays open and the transaction stays usable.

9. **Compatibility.** Behaviour outside transactions must not change, and the existing test suite in `/app/tests` must keep passing. `db.storage` and `Table.storage` must still return the storage object the database was created with. Transactions must work with every storage, including `JSONStorage`, `MemoryStorage`, custom `Storage` subclasses and storages wrapped in middlewares such as `CachingMiddleware`.

Thread safety and concurrent access from several processes are out of scope.
