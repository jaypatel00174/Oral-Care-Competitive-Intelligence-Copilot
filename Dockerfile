# Backend image: the chat API (FastAPI) and the MCP server share it; only the command differs.
#
#   docker build -t oralcare-backend .
#   docker run --rm -p 127.0.0.1:8000:8000 --env-file .env oralcare-backend                 # API
#   docker run --rm -p 127.0.0.1:8765:8765 --env-file .env oralcare-backend \
#       python -m src.mcp_server.server --http                                               # MCP over HTTP
#   docker run --rm -i oralcare-backend python -m src.mcp_server.server                       # MCP over stdio
#
# The image is self-contained: the database and the search index are rebuilt at build time from the product
# data and the embedding model that are in the repository, so it needs no internet at run time and no volume.
# Model settings and the API key come from the environment (.env); they are never copied into the image.

FROM python:3.14-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HUB_DISABLE_SYMLINKS_WARNING=1 \
    HF_HUB_OFFLINE=1

WORKDIR /app

# Dependencies first, so code changes do not reinstall them.
COPY requirements.txt .
RUN pip install -r requirements.txt

# Code and the inputs needed to build the stores (no raw HTML, no secrets: see .dockerignore).
COPY src ./src
COPY data/products.json data/snippets.jsonl ./data/
COPY data/models ./data/models
COPY outputs/comparison_table.csv ./outputs/

# Rebuild SQLite and Chroma from products.json + snippets.jsonl. Offline: the embedding model is already in data/models.
RUN python -m src.pipeline.build_stores

# Run as an unprivileged user that owns the data (Chroma needs write access to its own files) and a log folder.
RUN useradd --create-home --uid 10001 app && mkdir -p /app/logs && chown -R app:app /app
USER app

# Inside a container the servers must listen on all of the container's interfaces, or no published port can
# reach them. Who can reach the port is decided where it is published (docker-compose.yml: the host's 127.0.0.1).
ENV HOST=0.0.0.0 \
    PORT=8000 \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8765

EXPOSE 8000 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import json,urllib.request; d=json.load(urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)); raise SystemExit(0 if d.get('status') == 'ok' else 1)" || exit 1

CMD ["python", "-m", "src.app.api"]
