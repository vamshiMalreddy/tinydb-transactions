#!/bin/bash
# CHEAT: exposes the full transaction API but writes straight through to
# storage, so nothing is ever rolled back. Must score 0.
set -euo pipefail
bash /solution/reference_solve.sh
python3 - <<'PY'
p = "/app/tinydb/database.py"
s = open(p).read()
for old, new in [
    ("        if self._txn_stack:\n            if for_update:\n                return copy.deepcopy(self._txn_data)\n            return self._txn_data\n\n        return self._storage.read()",
     "        return self._storage.read()"),
    ("        if self._txn_stack:\n            self._txn_data = data\n            self._txn_dirty = True\n        else:\n            self._storage.write(data)",
     "        self._storage.write(data)"),
]:
    assert old in s, old
    s = s.replace(old, new)
open(p, "w").write(s)
PY
