FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

ARG NL2SQL_EXTRAS=""

RUN pip install --no-cache-dir -U pip setuptools wheel \
    && if [ -n "$NL2SQL_EXTRAS" ]; then \
        pip install --no-cache-dir "nl2sql-core[$NL2SQL_EXTRAS]" nl2sql-api; \
       else \
        pip install --no-cache-dir nl2sql-core nl2sql-api; \
       fi

EXPOSE 8000

CMD ["uvicorn", "nl2sql_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
