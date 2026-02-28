#!/usr/bin/env bash
# CostPilot — Install Script
# Creates configs, sets permissions, and verifies requirements

set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${CYAN}⚡ CostPilot — Install${NC}"
echo "──────────────────────────────────"

# Check Python version
PY=$(python3 --version 2>&1 | awk '{print $2}')
PY_MAJOR=$(echo "$PY" | cut -d. -f1)
PY_MINOR=$(echo "$PY" | cut -d. -f2)

if [[ "$PY_MAJOR" -lt 3 || ("$PY_MAJOR" -eq 3 && "$PY_MINOR" -lt 9) ]]; then
  echo -e "${RED}✗ Python 3.9+ required (found $PY)${NC}"
  exit 1
fi
echo -e "${GREEN}✓ Python $PY${NC}"

# Install Python dependencies
if [[ -f "$DIR/requirements.txt" ]]; then
  echo -e "${YELLOW}→ Installing Python dependencies...${NC}"
  pip install -r "$DIR/requirements.txt" --quiet && echo -e "${GREEN}✓ Dependencies installed${NC}" || {
    echo -e "${RED}✗ pip install failed. Run manually: pip install -r requirements.txt${NC}"
    exit 1
  }
fi

# Create config.json if missing
if [[ ! -f "$DIR/config.json" ]]; then
  echo -e "${YELLOW}→ Creating config.json from example...${NC}"
  cp "$DIR/config.example.json" "$DIR/config.json"
  # Strip _comment field
  python3 -c "
import json
with open('config.json') as f: c = json.load(f)
c.pop('_comment', None)
with open('config.json','w') as f: json.dump(c, f, indent=2)
"
  echo -e "${GREEN}✓ config.json created${NC}"
else
  echo -e "${GREEN}✓ config.json exists${NC}"
fi

# Create demo data if cost-events.jsonl missing
if [[ ! -f "$DIR/cost-events.jsonl" ]]; then
  echo -e "${YELLOW}→ No cost-events.jsonl found. Demo data will be used.${NC}"
  echo -e "  Run: python3 auto_logger.py${NC}"
else
  LINES=$(wc -l < "$DIR/cost-events.jsonl")
  echo -e "${GREEN}✓ cost-events.jsonl exists ($LINES events)${NC}"
fi

# Create backups directory
mkdir -p "$DIR/backups"
echo -e "${GREEN}✓ backups/ directory ready${NC}"

# Set executable permissions
chmod +x "$DIR/install.sh" 2>/dev/null || true
chmod +x "$DIR/uninstall.sh" 2>/dev/null || true
chmod +x "$DIR/auto_logger.py" 2>/dev/null || true
chmod +x "$DIR/server.py" 2>/dev/null || true
echo -e "${GREEN}✓ Executable permissions set${NC}"

# Check if server is already running
if lsof -i:8742 &>/dev/null 2>&1; then
  echo -e "${YELLOW}⚠ Something already running on port 8742${NC}"
else
  echo -e "${GREEN}✓ Port 8742 available${NC}"
fi

echo ""
echo -e "${CYAN}✅ Installation complete!${NC}"
echo ""
echo "Next steps:"
echo -e "  ${CYAN}1.${NC} Start server:    python3 server.py"
echo -e "  ${CYAN}2.${NC} Log sessions:    python3 auto_logger.py"
echo -e "  ${CYAN}3.${NC} Open dashboard:  http://localhost:8742"
echo ""
echo "Or use make:"
echo -e "  ${CYAN}make start${NC}   — start in background"
echo -e "  ${CYAN}make test${NC}    — run all tests"
echo -e "  ${CYAN}make health${NC}  — check server health"
