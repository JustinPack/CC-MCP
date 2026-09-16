FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --home-dir /nonexistent \
        --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY LICENSE NOTICE.md ./
COPY server/mcp_server.py server/reference_store.py ./server/
COPY server/data/cc14_index.db ./server/data/

# The server and its reference database are never writable at runtime.
RUN chmod -R a=rX /app

USER app
ENTRYPOINT ["python", "/app/server/mcp_server.py"]
