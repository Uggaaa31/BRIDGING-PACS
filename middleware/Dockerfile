# ==============================================================================
# Dockerfile - PACS-SIMGOS HL7 Middleware (Python 3.10)
# ==============================================================================
FROM python:3.10-slim

# Set environment
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8

WORKDIR /app

# Install dependensi sistem jika diperlukan (curl untuk healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install dependensi Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code middleware
COPY . .

# Buat direktori logs + data (antrean spooler) jika belum ada
RUN mkdir -p /app/logs /app/data

# Expose port MLLP HL7
EXPOSE 2575

# Jalankan MLLP server
CMD ["python", "main.py"]
