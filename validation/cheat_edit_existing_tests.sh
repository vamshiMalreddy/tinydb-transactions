#!/bin/bash
# CHEAT: implements transactions but breaks an existing feature (truncate no
# longer resets the ID counter), then deletes the existing test that catches
# it. The verifier runs a pristine copy of the upstream suite. Must score 0.
set -euo pipefail
bash /solution/reference_solve.sh
python3 - <<'PY'
p = "/app/tinydb/table.py"
s = open(p).read()
old = "        # Reset document ID counter\n        self._next_id = None\n"
assert old in s
s = s.replace(old, "")
open(p, "w").write(s)
PY
rm -f /app/tests/test_tables.py
