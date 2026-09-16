FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY LICENSE NOTICE.md ./
COPY server/mcp_server.py server/reference_store.py ./server/
COPY server/data/cc14_index.db ./server/data/

USER 10001:10001
CMD ["python", "server/mcp_server.py"]
