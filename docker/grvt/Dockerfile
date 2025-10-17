FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements for GRVT
COPY docker/grvt/requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code
COPY exchanges/ /app/exchanges/
COPY helpers/ /app/helpers/
COPY docker/grvt/grvt_service.py /app/

# Expose API port
EXPOSE 8001

# Run the GRVT service
CMD ["python", "grvt_service.py"]
