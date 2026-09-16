FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MCP_TRANSPORT=streamable-http \
    MCP_HOST=0.0.0.0 \
    PORT=8000

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py .

RUN useradd --create-home --shell /usr/sbin/nologin appuser && \
    mkdir -p /data && chown -R appuser:appuser /app /data
USER appuser
VOLUME ["/data"]
EXPOSE 8000
CMD ["python", "server.py"]
