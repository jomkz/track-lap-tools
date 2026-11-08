#!/usr/bin/env bash
# Pit lap viewer for Linux (tested setup: Fedora). First run installs the Python packages (needs internet once).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HERE/.venv"
if [ ! -f "$VENV/ready.txt" ]; then
  echo "Setting up the lap viewer (needs internet the first time)..."
  rm -rf "$VENV"
  python3 -m venv "$VENV"
  "$VENV/bin/python" -m pip install --upgrade pip
  if ! "$VENV/bin/python" -m pip install -r "$HERE/../requirements.txt"; then
    rm -rf "$VENV"; echo "Package install failed; check the connection and run again."; exit 1
  fi
  echo ok > "$VENV/ready.txt"
fi
exec "$VENV/bin/python" -I "$HERE/lapviewer.py" "$@"
