# TinyDB Transactions

## Overview
The agent is given an unmodified copy of [TinyDB](https://github.com/msiemens/tinydb) 4.8.2 (MIT licence), a ~2k-line pure-Python document database. It must add **atomic, nested transactions with savepoints**, following the specification in `instruction.md`.

## Skills tested
- **Codebase comprehension.** All storage access is funnelled through `Table._read_table` / `Table._update_table` and a few places in `TinyDB`. The agent has to find every one of them, including the write that happens inside `Table.__init__` for `persist_empty=True`.
- **State management.** On rollback, the agent must restore the per-table document-ID counters (`Table._next_id`) and invalidate every query cache. That includes tables the caller still holds after `drop_tables()`, and tables first created inside the transaction.
- **Aliasing bugs.** `MemoryStorage`, `CachingMiddleware` and many custom storages return their live internal dict from `read()`, and TinyDB mutates documents in place. A naive "buffer the write" implementation silently corrupts committed data.
- **Failure handling.** Inside a transaction, a write that raises part-way through (e.g. an `update` callable that fails on the second document) must leave no half-applied changes, even though TinyDB mutates documents in place. If `storage.write()` raises on the outermost commit, the database must end up exactly as if the transaction had been rolled back: ID counters, query caches and tables created inside the transaction included. If `storage.read()` raises on enter, no transaction may be left active.
- **API design under constraints.** The feature must stay fully backward compatible and pass the upstream test suite unchanged.

## Environment
- Base image: `python:3.12-slim-bookworm`, with `git`, `pytest` and `pytest-cov` installed
- Working directory: `/app` (TinyDB source, committed to a fresh git repo; `PYTHONPATH=/app`)

## Verification
The verifier runs in a separate container built from `tests/Dockerfile` (pytest pre-installed); the agent's `/app` is copied in via `artifacts`. `tests/test.sh` runs two suites from `/tests` against `/app`:
1. `test_outputs.py`: 56 hidden behavioural test functions (203 cases once parametrised), most of them run across four storage set-ups. The set-ups are `MemoryStorage`, `JSONStorage`, `CachingMiddleware(MemoryStorage)` (each wrapped in a write-counting middleware) and a custom `Storage` subclass. The failure tests use the same four set-ups, wrapped in a middleware (or custom storage) that can be told to raise from `read()` or `write()`.
2. `upstream_tests/`: TinyDB's own test suite, taken from a pristine copy so that edits to `/app/tests` have no effect.

The reward is 1 only if every test passes.

## Reference solution
`solution/solve.sh` applies a patch of about 215 changed lines to `database.py`, `table.py` and `__init__.py`:
- `TinyDB` keeps a stack of `Transaction` objects and a deep-copied working state. Tables route reads and writes through `TinyDB._read_data` / `_write_data`.
- Each savepoint stores the working state, the dirty flag, the `_tables` map and every live table's `_next_id`. Live tables are tracked in a `WeakSet`.
- The outermost commit writes once, and only if something was written. If that write raises, the savepoint taken at the start is restored (tables, ID counters, caches) before the error propagates.
- Inside a transaction, write operations get a deep copy of the working state (`_read_data(for_update=True)`), so an operation that raises part-way through never touches it.

## Validation solutions (`validation/`)
Each script is swapped in for `solve.sh` by `scripts/validate_task.sh`; all must score 0:
- `cheat_write_through.sh`: the full transaction API, but every write goes straight to storage.
- `cheat_edit_existing_tests.sh`: breaks `truncate()` and deletes the existing test that catches it.
- `partial_shallow_snapshot.sh`: no deep copy on begin, so pending changes leak into `MemoryStorage`.
- `partial_reset_ids_on_rollback.sh`: resets ID counters instead of restoring them.
- `partial_persist_empty_leak.sh`: misses the write inside `Table.__init__`.
- `partial_write_on_empty_commit.sh`: writes on commit even when nothing changed.
- `partial_failed_op_leaks.sh`: write operations mutate the working state directly, so a failed `update` leaves half-applied changes.
- `partial_failed_commit_not_restored.sh`: a failed commit ends the transaction but leaves ID counters and caches in the uncommitted state.

There is no `alt_*` solution yet. A second, independently written implementation would be a good addition.

## Validating locally
```bash
cd ~/Documents/RLVR
chmod +x tasks/tinydb-transactions/*/*.sh
scripts/validate_task.sh tasks/tinydb-transactions
```
Expected: oracle scores 1; nop and every `validation/` script score 0.
`validate.sh` in this folder is a plain-Docker fallback that doesn't need Harbor.
