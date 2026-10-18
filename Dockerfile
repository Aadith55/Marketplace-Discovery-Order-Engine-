FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATABASE_URL=sqlite:////data/marketplace.db \
    APP_ENV=production
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && mkdir -p /data \
    && chown 10001:10001 /data
COPY --chown=10001:10001 marketplace ./marketplace
USER 10001:10001
EXPOSE 8000
CMD ["sh", "-c", "python -m marketplace.seed && uvicorn marketplace.main:app --host 0.0.0.0 --port 8000"]
