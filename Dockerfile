FROM python:3.11-slim

WORKDIR /app

# Системные зависимости для psycopg2-binary
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "bot:fast_app", "--host", "0.0.0.0", "--port", "8000"]
