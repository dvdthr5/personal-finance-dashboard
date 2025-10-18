# backend/Dockerfile
FROM python:3.12-slim

WORKDIR /app

# Install system dependencies for SSL & curl
RUN apt-get update && apt-get install -y ca-certificates curl && update-ca-certificates

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy environment and application code
COPY .env .env
COPY . .

# Start the FastAPI server
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
