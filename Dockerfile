FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 STOCKROOM_DATABASE=/data/stockroom.sqlite3
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --system stockroom \
    && useradd --system --gid stockroom stockroom \
    && install -d -o stockroom -g stockroom /data
COPY stockroom ./stockroom
COPY data ./data
USER stockroom
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).close()"]
CMD ["waitress-serve", "--listen=0.0.0.0:8000", "--call", "stockroom:create_app"]
