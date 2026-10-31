#!/bin/bash
# PARTIAL: misses the write inside Table.__init__ for persist_empty=True,
# so it goes straight to storage during a transaction. Must score 0.
set -euo pipefail
bash /solution/reference_solve.sh
python3 - <<'PY'
p = "/app/tinydb/database.py"
s = open(p).read()
old = "persist_empty = kwargs.pop(\"persist_empty\", False)".replace("\"", chr(39))
assert old in s
s = s.replace(old, "persist_empty = False")
open(p, "w").write(s)
PY
