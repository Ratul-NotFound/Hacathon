FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install system dependencies (CBC solver may need these on some platforms)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    coinor-cbc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/

# Expose port
EXPOSE 8000

# Run the application
# Bind to 0.0.0.0 for external accessibility
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
