#!/usr/bin/env bash
# KIRA Cost Cockpit — Uninstall Script
# Removes generated files but KEEPS your data

set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "KIRA Cost Cockpit — Uninstall"
echo "This will remove generated files but KEEP your data (*.jsonl, config.json)"
echo ""
read -p "Continue? [y/N] " yn
if [[ "$yn" != "y" && "$yn" != "Y" ]]; then
  echo "Aborted."
  exit 0
fi

# Stop server if running
PID_FILE=/tmp/kira-server.pid
if [[ -f "$PID_FILE" ]]; then
  echo "Stopping server..."
  kill "$(cat "$PID_FILE")" 2>/dev/null || true
  rm -f "$PID_FILE"
fi

# Remove Python cache
find "$DIR" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
find "$DIR" -name "*.pyc" -delete 2>/dev/null || true

# Remove PID/lock files
rm -f "$DIR/auto-logger.pid"

echo ""
echo "✓ Generated files removed."
echo ""
echo "KEPT (your data):"
echo "  config.json"
echo "  cost-events.jsonl"
echo "  auto-logger-state.json"
echo "  backups/"
echo ""
echo "To fully remove, delete the cost-cockpit/ directory."
