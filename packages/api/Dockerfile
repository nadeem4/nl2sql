# Builds the API image from this repository.
#
# Build context is the repo root:
#   docker build -f packages/api/Dockerfile -t nl2sql-api .
#
# The Python here has to satisfy every package's `requires-python` (>=3.12);
# tests/unit/test_docker_images.py fails when it does not, and the `api-image`
# job in test.yml builds this image on every pull request.
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY ./packages/adapter-sdk /app/packages/adapter-sdk
COPY ./packages/nl2sql /app/packages/nl2sql
COPY ./packages/api /app/packages/api

RUN pip install --no-cache-dir -U pip setuptools wheel \
    && pip install --no-cache-dir \
        ./packages/adapter-sdk \
        "./packages/nl2sql[postgres,mysql]" \
        ./packages/api

EXPOSE 8000

CMD ["uvicorn", "nl2sql_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
