#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec uv run --frozen --no-dev --project "$SCRIPT_DIR/.." python "$SCRIPT_DIR/desktop.py" "$@"
