#!/bin/bash
# Local sanity check for the task (not part of the benchmark itself).
#   1. Builds the environment image.
#   2. Runs the verifier on the untouched codebase  -> expected reward 0.
#   3. Runs solve.sh, then the verifier             -> expected reward 1.
set -euo pipefail
cd "$(dirname "$0")"

IMAGE=tinydb-transactions:local
docker build -t "$IMAGE" environment

run_verifier () {
  local label=$1 pre=$2
  echo "=== $label ==="
  docker run --rm \
    -v "$PWD/tests:/tests:ro" \
    -v "$PWD/solution:/solution:ro" \
    "$IMAGE" bash -c "
      mkdir -p /logs/verifier
      $pre
      bash /tests/test.sh > /logs/verifier/out.txt 2>&1
      grep -oE '^(PASSED|FAILED|ERROR)' /logs/verifier/out.txt | sort | uniq -c
      tail -3 /logs/verifier/out.txt
      echo reward=\$(cat /logs/verifier/reward.txt)
    "
}

run_verifier "baseline (no solution, expect reward=0)" "true"
run_verifier "reference solution (expect reward=1)"   "bash /solution/solve.sh"
