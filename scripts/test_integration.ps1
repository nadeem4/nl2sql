$env:POSTGRES_CONNECTION_STRING = "postgresql+psycopg2://user:password@localhost:5432/manufacturing_ops"
$env:MYSQL_CONNECTION_STRING = "mysql+pymysql://user:password@localhost:3306/manufacturing_supply"
$env:MSSQL_CONNECTION_STRING = "mssql+pyodbc://sa:StrongPass2023!@localhost:1433/master?driver=ODBC+Driver+17+for+SQL+Server&TrustServerCertificate=yes"

Write-Host "Running SQLite Tests (Local)..." -ForegroundColor Cyan
python -m pytest packages/adapters/sqlite/tests

Write-Host "Running Postgres Tests (Docker)..." -ForegroundColor Cyan
python -m pytest packages/adapters/postgres/tests

Write-Host "Running MySQL Tests (Docker)..." -ForegroundColor Cyan
python -m pytest packages/adapters/mysql/tests

Write-Host "Running MSSQL Tests (Docker)..." -ForegroundColor Cyan
python -m pytest packages/adapters/mssql/tests
