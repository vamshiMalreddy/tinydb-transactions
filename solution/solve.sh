#!/bin/bash
# Reference solution: adds nested, atomic transactions to TinyDB.
set -euo pipefail
cd /app

cat > /tmp/transactions.patch <<'PATCH_EOF'
diff --git a/tinydb/__init__.py b/tinydb/__init__.py
index 0416e2e..0a38462 100644
--- a/tinydb/__init__.py
+++ b/tinydb/__init__.py
@@ -26,7 +26,8 @@ Usage example:
 
 from .queries import Query, where
 from .storages import Storage, JSONStorage
-from .database import TinyDB
+from .database import TinyDB, Transaction, TransactionError
 from .version import __version__
 
-__all__ = ('TinyDB', 'Storage', 'JSONStorage', 'Query', 'where')
+__all__ = ('TinyDB', 'Storage', 'JSONStorage', 'Query', 'where',
+           'Transaction', 'TransactionError')
diff --git a/tinydb/database.py b/tinydb/database.py
index 4a73c46..1379dc4 100644
--- a/tinydb/database.py
+++ b/tinydb/database.py
@@ -1,7 +1,9 @@
 """
 This module contains the main component of TinyDB: the database.
 """
-from typing import Dict, Iterator, Set, Type
+import copy
+import weakref
+from typing import Any, Dict, Iterator, List, Optional, Set, Type
 
 from . import JSONStorage
 from .storages import Storage
@@ -13,6 +15,80 @@ from .utils import with_typehint
 TableBase: Type[Table] = with_typehint(Table)
 
 
+class TransactionError(Exception):
+    """
+    Raised when a transaction is used incorrectly.
+    """
+
+
+class Transaction:
+    """
+    A (possibly nested) database transaction.
+
+    Use it as a context manager obtained from
+    :meth:`~tinydb.database.TinyDB.transaction`::
+
+        with db.transaction() as txn:
+            db.insert({'a': 1})
+
+    All writes inside the block are committed when the block exits normally
+    and discarded when it exits with an exception or when :meth:`rollback`
+    is called.
+    """
+
+    _NEW = 'new'
+    _ACTIVE = 'active'
+    _DONE = 'done'
+
+    def __init__(self, database: 'TinyDB'):
+        self._db = database
+        self._state = self._NEW
+
+        # Savepoint data captured on entry
+        self._data: Any = None
+        self._dirty = False
+        self._tables: Dict[str, Table] = {}
+        self._next_ids: Dict[Table, Optional[int]] = {}
+
+    @property
+    def active(self) -> bool:
+        """
+        Whether this transaction has been entered and not yet finished.
+        """
+        return self._state == self._ACTIVE
+
+    def rollback(self) -> None:
+        """
+        Discard all changes made in this transaction and end it.
+        """
+        if not self.active:
+            raise TransactionError('Transaction is not active')
+
+        if self._db._txn_stack[-1] is not self:
+            raise TransactionError(
+                'Cannot roll back while a nested transaction is active'
+            )
+
+        self._db._rollback_transaction(self)
+
+    def __enter__(self) -> 'Transaction':
+        if self._state != self._NEW:
+            raise TransactionError('A transaction can only be entered once')
+
+        self._db._begin_transaction(self)
+        return self
+
+    def __exit__(self, exc_type, exc_value, traceback) -> bool:
+        if self.active:
+            if exc_type is None:
+                self._db._commit_transaction(self)
+            else:
+                self._db._rollback_transaction(self)
+
+        # Never suppress exceptions
+        return False
+
+
 class TinyDB(TableBase):
     """
     The main class of TinyDB.
@@ -88,6 +164,13 @@ class TinyDB(TableBase):
         Create a new instance of TinyDB.
         """
 
+        # Transaction state. Set up first, because ``__getattr__`` forwards
+        # unknown attributes to the default table.
+        self._txn_stack: List[Transaction] = []
+        self._txn_data: Any = None
+        self._txn_dirty = False
+        self._all_tables: 'weakref.WeakSet[Table]' = weakref.WeakSet()
+
         storage = kwargs.pop('storage', self.default_storage_class)
 
         # Prepare the storage
@@ -127,9 +210,19 @@ class TinyDB(TableBase):
         if name in self._tables:
             return self._tables[name]
 
+        # ``persist_empty`` writes during construction, so we handle it
+        # ourselves once the table is connected to this database. That way
+        # the write takes part in an active transaction.
+        persist_empty = kwargs.pop('persist_empty', False)
+
         table = self.table_class(self.storage, name, **kwargs)
+        table._database = self
+        self._all_tables.add(table)
         self._tables[name] = table
 
+        if persist_empty:
+            table._update_table(lambda data: data.clear())
+
         return table
 
     def tables(self) -> Set[str]:
@@ -158,7 +251,7 @@ class TinyDB(TableBase):
         # so we need to consider this case to and return an empty set in this
         # case.
 
-        return set(self.storage.read() or {})
+        return set(self._read_data() or {})
 
     def drop_tables(self) -> None:
         """
@@ -167,7 +260,7 @@ class TinyDB(TableBase):
 
         # We drop all tables from this database by writing an empty dict
         # to the storage thereby returning to the initial state with no tables.
-        self.storage.write({})
+        self._write_data({})
 
         # After that we need to remember to empty the ``_tables`` dict, so we'll
         # create new table instances when a table is accessed again.
@@ -185,7 +278,7 @@ class TinyDB(TableBase):
         if name in self._tables:
             del self._tables[name]
 
-        data = self.storage.read()
+        data = self._read_data()
 
         # The database is uninitialized, there's nothing to do
         if data is None:
@@ -199,7 +292,7 @@ class TinyDB(TableBase):
         del data[name]
 
         # Store the updated data back to the storage
-        self.storage.write(data)
+        self._write_data(data)
 
     @property
     def storage(self) -> Storage:
@@ -226,9 +319,133 @@ class TinyDB(TableBase):
 
         Upon leaving this context, the ``close`` method will be called.
         """
+        if self._txn_stack:
+            raise TransactionError(
+                'Cannot close the database while a transaction is active'
+            )
+
         self._opened = False
         self.storage.close()
 
+    # --- Transactions -----------------------------------------------------
+
+    def transaction(self) -> Transaction:
+        """
+        Create a new transaction for use as a context manager.
+
+        Transactions may be nested; a nested transaction acts as a savepoint
+        inside the enclosing one.
+        """
+        return Transaction(self)
+
+    @property
+    def in_transaction(self) -> bool:
+        """
+        Whether a transaction is currently active on this database.
+        """
+        return bool(self._txn_stack)
+
+    def _read_data(self, for_update: bool = False):
+        """
+        Read the complete database state, honouring active transactions.
+
+        Inside a transaction, a caller that is about to modify the data
+        (``for_update``) gets a private deep copy. Write operations mutate
+        documents in place, so without the copy an operation that raises
+        part-way through would leave half-applied changes in the
+        transaction's working state.
+        """
+        if self._txn_stack:
+            if for_update:
+                return copy.deepcopy(self._txn_data)
+            return self._txn_data
+
+        return self._storage.read()
+
+    def _write_data(self, data) -> None:
+        """
+        Write the complete database state, honouring active transactions.
+        """
+        if self._txn_stack:
+            self._txn_data = data
+            self._txn_dirty = True
+        else:
+            self._storage.write(data)
+
+    def _begin_transaction(self, txn: Transaction) -> None:
+        if not self._txn_stack:
+            # Outermost transaction: take a private copy of the committed
+            # state so that nothing we do can leak into the storage (some
+            # storages return their internal objects from ``read()``).
+            self._txn_data = copy.deepcopy(self._storage.read())
+            self._txn_dirty = False
+
+        # Remember everything needed to return to this point
+        txn._data = copy.deepcopy(self._txn_data)
+        txn._dirty = self._txn_dirty
+        txn._tables = dict(self._tables)
+        txn._next_ids = {t: t._next_id for t in self._all_tables}
+        txn._state = Transaction._ACTIVE
+
+        self._txn_stack.append(txn)
+
+    def _unwind_above(self, txn: Transaction) -> None:
+        """
+        Roll back any transactions nested inside ``txn`` that are still
+        active (only possible when context managers exit out of order).
+        """
+        while self._txn_stack and self._txn_stack[-1] is not txn:
+            self._rollback_transaction(self._txn_stack[-1])
+
+    def _commit_transaction(self, txn: Transaction) -> None:
+        self._unwind_above(txn)
+
+        self._txn_stack.pop()
+        txn._state = Transaction._DONE
+
+        if not self._txn_stack:
+            # Outermost commit: persist the final state exactly once
+            data, dirty = self._txn_data, self._txn_dirty
+            self._txn_data = None
+            self._txn_dirty = False
+
+            if dirty:
+                try:
+                    self._storage.write(data)
+                except BaseException:
+                    # The commit failed: nothing new reached the storage,
+                    # so make the database look as if the transaction had
+                    # been rolled back, then let the error propagate.
+                    self._restore_savepoint(txn)
+                    raise
+
+    def _restore_savepoint(self, txn: Transaction) -> None:
+        """
+        Restore table instances and their document ID counters to what they
+        were when ``txn`` began, and drop every cached query result.
+        """
+        self._tables = dict(txn._tables)
+        for table in list(self._all_tables):
+            table._next_id = txn._next_ids.get(table, None)
+            table.clear_cache()
+
+    def _rollback_transaction(self, txn: Transaction) -> None:
+        self._unwind_above(txn)
+
+        self._txn_stack.pop()
+        txn._state = Transaction._DONE
+
+        self._restore_savepoint(txn)
+
+        if self._txn_stack:
+            # Return to the savepoint inside the enclosing transaction
+            self._txn_data = txn._data
+            self._txn_dirty = txn._dirty
+        else:
+            # Nothing was written to the storage, just forget the changes
+            self._txn_data = None
+            self._txn_dirty = False
+
     def __enter__(self):
         """
         Use the database as a context manager.
diff --git a/tinydb/table.py b/tinydb/table.py
index c562d02..c19197b 100644
--- a/tinydb/table.py
+++ b/tinydb/table.py
@@ -112,6 +112,13 @@ class Table:
             = self.query_cache_class(capacity=cache_size)
 
         self._next_id = None
+
+        # The database this table belongs to (set by ``TinyDB.table``). All
+        # storage access is routed through it so that transactions can
+        # buffer reads and writes. Standalone tables talk to the storage
+        # directly.
+        self._database = None
+
         if persist_empty:
             self._update_table(lambda table: table.clear())
 
@@ -705,7 +712,7 @@ class Table:
         """
 
         # Retrieve the tables from the storage
-        tables = self._storage.read()
+        tables = self._read_storage()
 
         if tables is None:
             # The database is empty
@@ -734,7 +741,7 @@ class Table:
         document class, as the table data will *not* be returned to the user.
         """
 
-        tables = self._storage.read()
+        tables = self._read_storage(for_update=True)
 
         if tables is None:
             # The database is empty
@@ -767,7 +774,25 @@ class Table:
         }
 
         # Write the newly updated data back to the storage
-        self._storage.write(tables)
+        self._write_storage(tables)
 
         # Clear the query cache, as the table contents have changed
         self.clear_cache()
+
+    def _read_storage(self, for_update: bool = False):
+        """
+        Read the complete database state, honouring active transactions.
+        """
+        if self._database is not None:
+            return self._database._read_data(for_update)
+
+        return self._storage.read()
+
+    def _write_storage(self, tables) -> None:
+        """
+        Write the complete database state, honouring active transactions.
+        """
+        if self._database is not None:
+            self._database._write_data(tables)
+        else:
+            self._storage.write(tables)
PATCH_EOF

git apply /tmp/transactions.patch
rm /tmp/transactions.patch
