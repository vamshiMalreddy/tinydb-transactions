#!/bin/bash
# Runs in the separate verifier container (see task.toml: verifier.environment_mode
# = "separate", built from tests/Dockerfile which pre-installs pytest).
#
# /app is copied in from the agent's sandbox via `artifacts` in task.toml.
# /tests is a pristine copy of this directory, so any edits the agent made under
# /app/tests have no effect: the grading suites below come from /tests.
mkdir -p /logs/verifier

# Grade the agent's code in /app against:
#   1. test_outputs.py      - hidden behavioural tests for the feature
#   2. upstream_tests/       - TinyDB's own suite (regression), a pristine copy
#
# Run from /tests with --rootdir/--confcutdir pinned there. Do NOT use
# `-c /dev/null`: it makes /dev the rootdir, which stops upstream_tests/conftest.py
# from loading and leaks test_outputs.py's `db` fixture into the upstream tests.
# No ini file exists in /tests or above it, so /app/pytest.ini (which needs
# pytest-cov) is never picked up.
export PYTHONPATH=/app
cd /tests

if pytest \
    --rootdir=/tests --confcutdir=/tests -o addopts="" -p no:cacheprovider \
    --ctrf /logs/verifier/ctrf.json \
    test_outputs.py upstream_tests -rA; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
