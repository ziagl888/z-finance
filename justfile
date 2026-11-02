# Dev tasks — run `just` to list recipes. https://just.systems

# Recipe shebangs are `#!/usr/bin/env sh`, never bash: WSL puts bash.exe in
# System32, so `env bash` resolves to the WSL launcher when just runs from
# PowerShell. WSL ships no sh.exe; on Windows `sh` comes from Git for Windows.
# POSIX sh has no arrays and no `pipefail`: use `set -- ...` / `for x in "$@"`.

# List available recipes
default:
    @just --list --unsorted

# Install Python (uv) and frontend (npm) dependencies from the lock files
setup:
    #!/usr/bin/env sh
    set -eu
    command -v uv >/dev/null || { echo "uv is required: https://docs.astral.sh/uv/"; exit 1; }
    command -v npm >/dev/null || { echo "Node.js 20+ with npm is required: https://nodejs.org/"; exit 1; }
    uv sync
    (cd frontend && npm ci)

# Backend with auto-reload on :8765 plus Vite dev server on :5173 (proxies /api); data in ./data
dev:
    #!/usr/bin/env sh
    set -eu
    # Developer data never goes to the real per-user directory (UC NF2).
    export ZFINANCE_DATA_DIR="${ZFINANCE_DATA_DIR:-$PWD/data}"
    uv run z-finance --reload &
    backend=$!
    trap 'kill "$backend" 2>/dev/null || true' EXIT INT TERM
    cd frontend && npm run dev

# Lint and format-check Python, type-check the frontend
lint:
    uv run ruff check .
    uv run ruff format --check .
    cd frontend && npm run typecheck

# Apply ruff fixes and formatting
fmt:
    uv run ruff check --fix .
    uv run ruff format .

# Run the Python test suite (extra args go to pytest, e.g. `just test -k health`)
test *args:
    uv run pytest -q {{ args }}

# Build the SPA bundle into backend/zfinance/web/ (served by the backend)
build:
    cd frontend && npm run build

# Re-extract the translation catalogs (frontend + backend) after adding or changing UI strings
i18n:
    uv run python tools/check_pot_freshness.py --write

# Build the bundle and start the app like an end user would (opens the browser)
run: build
    uv run z-finance
