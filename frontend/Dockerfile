# frontend/Dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
ENV PIP_DEFAULT_TIMEOUT=100
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8501

CMD ["streamlit", "run", "Home Page.py", "--server.port=8501", "--server.address=0.0.0.0"]