FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for newspaper3k and lxml
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libxml2-dev \
    libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy and install dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Copy application code
COPY . .

# Create data directory
RUN mkdir -p /app/data

EXPOSE 8000

CMD ["python", "app.py"]
