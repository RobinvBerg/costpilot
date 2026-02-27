FROM python:3.11-slim

LABEL maintainer="Robin Berg"
LABEL description="KIRA Cost Cockpit — AI spend monitoring dashboard"
LABEL version="1.2"

WORKDIR /app

# Copy application files
COPY server.py .
COPY dashboard.html .
COPY config.example.json config.json

# Create directories
RUN mkdir -p /data /app/backups

# Set data file to persistent volume path
ENV KIRA_DATA_FILE=/data/cost-events.jsonl
ENV KIRA_CONFIG_FILE=/app/config.json

# Expose port
EXPOSE 8742

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8742/api/ping')"

# Run server
CMD ["python3", "server.py", \
     "--host", "0.0.0.0", \
     "--port", "8742", \
     "--data-file", "/data/cost-events.jsonl", \
     "--config-file", "/app/config.json"]
