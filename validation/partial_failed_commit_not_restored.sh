#!/bin/bash
# PARTIAL: when storage.write() raises on the outermost commit, the
# transaction ends but table ID counters and query caches keep the
# uncommitted state, so the database shows data that was never saved.
# Must score 0.
set -euo pipefail
bash /solution/reference_solve.sh
python3 - <<'PY'
p = "/app/tinydb/database.py"
s = open(p).read()
old = "                    self._restore_savepoint(txn)\n                    raise\n"
assert old in s
s = s.replace(old, "                    raise\n")
open(p, "w").write(s)
PY
