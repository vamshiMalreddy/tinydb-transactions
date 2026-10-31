#!/bin/bash
# PARTIAL: always writes on the outermost commit, even if nothing changed.
# The spec says a transaction with no writes must not call write(). Must score 0.
set -euo pipefail
bash /solution/reference_solve.sh
python3 - <<'PY'
p = "/app/tinydb/database.py"
s = open(p).read()
old = "            if dirty:\n                try:\n                    self._storage.write(data)"
assert old in s
s = s.replace(old, "            if True:\n                try:\n                    self._storage.write(data if dirty else self._storage.read())")
open(p, "w").write(s)
PY
