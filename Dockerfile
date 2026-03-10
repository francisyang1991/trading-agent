# Trading Agent Docker Image
# Multi-stage build for smaller final image

FROM python:3.11-slim as builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Final stage
FROM python:3.11-slim

WORKDIR /app

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH

# Copy application code
COPY src/ ./src/
COPY tools/ ./tools/
COPY config/ ./config/
COPY main.py .
COPY backtest_runner.py .

# Create data directories
RUN mkdir -p /app/data /app/logs

# Default environment variables
ENV IB_HOST=ibgateway
ENV IB_PORT=4002
ENV IB_CLIENT_ID=1
ENV IB_TRADING_MODE=paper
ENV GUI_PORT=8080
ENV PYTHONUNBUFFERED=1

# Expose GUI port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/api/health || exit 1

# Default command: run trading GUI
CMD ["python", "-m", "tools.trading_gui"]
