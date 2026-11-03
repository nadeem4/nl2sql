# The API image: nl2sql-api with its web app, all five adapters and the SQL Server ODBC driver (D213, D226, D233).
#
# Build context is the repository root:
#   docker build -t nl2sql-api .
#
# Stage one builds nl2sql-ui; stage two installs the Python packages from this repository and serves the build.
# The Python here has to satisfy every package's `requires-python` (>=3.12);
# packages/nl2sql/tests/unit/test_docker_images.py fails when it does not, and the `api-image` job in test.yml builds
# this image on every pull request.

# --- Stage one: the web app. `npm run build` writes into the API package (nl2sql-ui/vite.config.js).
FROM node:22-slim AS ui
WORKDIR /build/nl2sql-ui
COPY nl2sql-ui/package.json nl2sql-ui/package-lock.json ./
RUN npm ci
COPY nl2sql-ui/ ./
RUN mkdir -p /build/packages/api/src/nl2sql_api/web && npm run build

# --- Stage two: the API.
FROM python:3.12-slim-bookworm

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    NL2SQL_CONFIG=/etc/nl2sql/nl2sql.yaml

# Microsoft's ODBC driver for SQL Server (Debian 12, from Microsoft's repository) and unixODBC.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl gnupg ca-certificates unixodbc \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && curl -fsSL https://packages.microsoft.com/config/debian/12/prod.list -o /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
    && apt-get purge -y --auto-remove curl gnupg \
    && rm -rf /var/lib/apt/lists/*

COPY ./packages/sdk /app/packages/sdk
COPY ./packages/nl2sql /app/packages/nl2sql
COPY ./packages/api /app/packages/api
COPY ./nl2sql-cli /app/nl2sql-cli
# The page nl2sql-api serves, built in stage one.
COPY --from=ui /build/packages/api/src/nl2sql_api/web/static /app/packages/api/src/nl2sql_api/web/static

RUN pip install -U pip setuptools wheel \
    && pip install \
        ./packages/sdk \
        "./packages/nl2sql[postgres,mysql,mssql,duckdb,anthropic,otel]" \
        ./packages/api \
        ./nl2sql-cli

# Non-root. /data is the volume for storage.path (docs/deployment/compose.md).
RUN useradd --create-home --uid 1000 nl2sql \
    && mkdir -p /data /opt/nl2sql \
    && chown nl2sql /data /opt/nl2sql
USER nl2sql
ENV HOME=/home/nl2sql

# The sample databases (Chinook, Support, Web Analytics), and the local embedding model: indexing them with the local
# embedder is what pulls the ONNX model into $HOME/.cache/chroma, so no first question waits for a download. The index
# itself is thrown away; the config you mount decides what is indexed.
RUN cd /opt/nl2sql \
    && nl2sql init --demo samples \
    && nl2sql --config samples/nl2sql.yaml index \
    && rm -rf samples/.nl2sql "$HOME/.cache/chroma/onnx_models/all-MiniLM-L6-v2/onnx.tar.gz"

# The config is a folder mounted at /etc/nl2sql: nl2sql.yaml and the files it includes. Point storage.path at the data
# volume, for example `path: "${env:NL2SQL_DATA_DIR}"` with NL2SQL_DATA_DIR=/data:
#   docker run -v ./config:/etc/nl2sql:ro -v nl2sql-data:/data \
#     -e NL2SQL_DATA_DIR=/data -p 8000:8000 nl2sql-api
EXPOSE 8000

# Ready, not merely alive: 503 until the index exists and the required datasources answer.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/ready', timeout=4)"

# `serve`, not bare uvicorn: the mode checks and the .env load live there. One worker: jobs and the state they share
# live in this process (D205).
CMD ["nl2sql-api", "serve", "--host", "0.0.0.0", "--port", "8000"]
