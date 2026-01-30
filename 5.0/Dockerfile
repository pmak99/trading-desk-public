# Trading Desk 5.0 - Autopilot
# Cloud Run compatible container

FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies (gcc for building, curl for healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first (for caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src/ ./src/

# Copy data directory with historical moves database
COPY data/ ./data/

# Set Python path
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Cloud Run expects port 8080
ENV PORT=8080
EXPOSE 8080

# Health check (use curl instead of Python for reliability)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/ || exit 1

# Run the application
CMD ["python", "-m", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8080"]
