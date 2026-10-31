#!/bin/bash
# PARTIAL: buffers writes but does not copy the committed state on begin.
# MemoryStorage hands out its live dict and TinyDB edits documents in place,
# so pending changes leak into storage. Must score 0.
set -euo pipefail
bash /solution/reference_solve.sh
python3 - <<'PY'
p = "/app/tinydb/database.py"
s = open(p).read()
old = "self._txn_data = copy.deepcopy(self._storage.read())"
assert old in s
s = s.replace(old, "self._txn_data = self._storage.read()")
open(p, "w").write(s)
PY
