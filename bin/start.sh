#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
exec python bin/server.py --port 8766 "$@"
