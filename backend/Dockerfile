# backend/Dockerfile
FROM python:3.11-slim

# ---------- system packages ----------
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc postgresql-client ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# ---------- Python deps ----------
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ---------- project code ----------
WORKDIR /app
COPY . /app/backend            

ENV PYTHONPATH=/app:/app/backend
EXPOSE 8000

CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]