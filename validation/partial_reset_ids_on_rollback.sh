#!/bin/bash
# PARTIAL: on rollback, resets each table's ID counter to None instead of
# restoring the saved value, so a removed top ID gets reused. Must score 0.
set -euo pipefail
bash /solution/reference_solve.sh
python3 - <<'PY'
p = "/app/tinydb/database.py"
s = open(p).read()
old = "table._next_id = txn._next_ids.get(table, None)"
assert old in s
s = s.replace(old, "table._next_id = None")
open(p, "w").write(s)
PY
