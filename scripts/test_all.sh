#!/usr/bin/env bash
set -eo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="$ROOT_DIR/services/gemflow:$ROOT_DIR/packages/egress:$ROOT_DIR/apps/tokenflow${PYTHONPATH:+:$PYTHONPATH}"

cd "$ROOT_DIR"
python3 -m unittest discover -s services/gemflow/tests -p "test_*.py"
python3 -m unittest discover -s packages/egress/tests -p "test_*.py"
python3 -m unittest discover -s apps/tokenflow/tests -p "test_*.py"
python3 -m unittest discover -s tests -p "test_*.py"
