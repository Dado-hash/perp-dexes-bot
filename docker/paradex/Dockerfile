FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements for Paradex
COPY docker/paradex/requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code
COPY exchanges/ /app/exchanges/
COPY helpers/ /app/helpers/
COPY docker/paradex/paradex_service.py /app/

# Expose API port
EXPOSE 8002

# Run the Paradex service
CMD ["python", "paradex_service.py"]
