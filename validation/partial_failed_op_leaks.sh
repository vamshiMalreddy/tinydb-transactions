#!/bin/bash
# PARTIAL: write operations inside a transaction work directly on the
# transaction's working copy, so an update callable that raises part-way
# through leaves the documents it already changed modified. Must score 0.
set -euo pipefail
bash /solution/reference_solve.sh
python3 - <<'PY'
p = "/app/tinydb/database.py"
s = open(p).read()
old = "                return copy.deepcopy(self._txn_data)"
assert old in s
s = s.replace(old, "                return self._txn_data")
open(p, "w").write(s)
PY
