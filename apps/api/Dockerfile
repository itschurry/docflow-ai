FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/apps/api

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY apps/api ./apps/api
COPY storage ./storage

RUN chmod +x /app/apps/api/docker-entrypoint.sh \
    && mkdir -p /app/storage/db /app/storage/uploads /app/storage/exports

EXPOSE 8000

ENTRYPOINT ["/app/apps/api/docker-entrypoint.sh"]
