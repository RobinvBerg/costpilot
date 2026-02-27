.PHONY: start stop install test clean logs

PORT ?= 8742
PID_FILE := /tmp/kira-server.pid

# Start the server in background
start:
	@echo "Starting KIRA Cost Cockpit..."
	@python3 server.py --port $(PORT) &
	@echo $$! > $(PID_FILE)
	@echo "Server running at http://localhost:$(PORT)"
	@echo "PID: $$(cat $(PID_FILE))"

# Stop the server
stop:
	@if [ -f $(PID_FILE) ]; then \
		echo "Stopping server (PID $$(cat $(PID_FILE)))..."; \
		kill $$(cat $(PID_FILE)) 2>/dev/null || true; \
		rm -f $(PID_FILE); \
		echo "Stopped"; \
	else \
		echo "Server not running (no PID file)"; \
	fi

# Install: check Python, create configs, set permissions
install:
	@./install.sh

# Run all tests
test:
	@echo "=== Running server smoke tests ==="
	@python3 test_server.py
	@echo ""
	@echo "=== Running auto-logger unit tests ==="
	@python3 test_auto_logger.py

# Log a test event
log-test:
	@python3 auto_logger.py --dry-run

# Clean generated files (NOT data)
clean:
	@find . -name "*.pyc" -delete
	@find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
	@echo "Cleaned .pyc and __pycache__"

# Show server logs (tail the server output if running)
logs:
	@echo "Tail server output — press Ctrl+C to stop"
	@tail -f /tmp/kira-server.log 2>/dev/null || echo "No log file found (server may be running without output redirect)"

# Open dashboard in browser
open:
	@open http://localhost:$(PORT) 2>/dev/null || xdg-open http://localhost:$(PORT) 2>/dev/null || echo "Open http://localhost:$(PORT)"

# Export current data to CSV
export:
	@curl -s "http://localhost:$(PORT)/api/export" -o kira-export-$$(date +%Y%m%d).csv
	@echo "Exported to kira-export-$$(date +%Y%m%d).csv"

# Archive old events
archive:
	@curl -s "http://localhost:$(PORT)/api/archive" | python3 -m json.tool

# Health check
health:
	@curl -s "http://localhost:$(PORT)/api/health" | python3 -m json.tool
