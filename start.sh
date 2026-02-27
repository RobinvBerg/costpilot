#!/bin/bash
# KIRA Cost Cockpit — Start Script
# Usage: ./start.sh

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

PORT=8742

# Kill any existing instance
if pkill -f "python3 server.py" 2>/dev/null; then
  echo "⚡ Stopping existing server..."
  sleep 0.5
fi

echo "🚀 Starting KIRA Cost Cockpit..."
python3 server.py &
SERVER_PID=$!

# Wait for server to be ready
sleep 1
if kill -0 $SERVER_PID 2>/dev/null; then
  echo "✅ Server running (PID: $SERVER_PID)"
  open "http://localhost:$PORT"
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  KIRA Cost Cockpit v1.0"
  echo "  Dashboard: http://localhost:$PORT"
  echo "  API:       http://localhost:$PORT/api/data"
  echo "  Export:    http://localhost:$PORT/api/export"
  echo "  PID: $SERVER_PID"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
else
  echo "❌ Server failed to start"
  exit 1
fi
