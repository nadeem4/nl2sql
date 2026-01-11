
# Templates for Demo Environment Generation

REF_SQL_POSTGRES = """
CREATE TABLE factories (
    id SERIAL PRIMARY KEY,
    name TEXT,
    region TEXT,
    capacity INTEGER
);
CREATE TABLE machine_types (
    id SERIAL PRIMARY KEY,
    model TEXT,
    producer TEXT,
    maintenance_interval_days INTEGER
);
CREATE TABLE shifts (
    id SERIAL PRIMARY KEY,
    name TEXT,
    start_time TEXT,
    end_time TEXT
);
"""

REF_SQL_SQLITE = """
CREATE TABLE factories (
    id INTEGER PRIMARY KEY,
    name TEXT,
    region TEXT,
    capacity INTEGER
);
CREATE TABLE machine_types (
    id INTEGER PRIMARY KEY,
    model TEXT,
    producer TEXT,
    maintenance_interval_days INTEGER
);
CREATE TABLE shifts (
    id INTEGER PRIMARY KEY,
    name TEXT,
    start_time TEXT,
    end_time TEXT
);
"""

OPS_SQL_POSTGRES = """
CREATE TABLE employees (
    id SERIAL PRIMARY KEY,
    name TEXT,
    factory_id INTEGER,
    shift_id INTEGER
);
CREATE TABLE machines (
    id SERIAL PRIMARY KEY,
    factory_id INTEGER,
    type_id INTEGER,
    status TEXT DEFAULT 'Active',
    installation_date DATE
);
CREATE TABLE maintenance_logs (
    id SERIAL PRIMARY KEY,
    machine_id INTEGER,
    date DATE,
    description TEXT,
    technician_id INTEGER
);
"""

OPS_SQL_SQLITE = """
CREATE TABLE employees (
    id INTEGER PRIMARY KEY,
    name TEXT,
    factory_id INTEGER,
    shift_id INTEGER
);
CREATE TABLE machines (
    id INTEGER PRIMARY KEY,
    factory_id INTEGER,
    type_id INTEGER,
    status TEXT,
    installation_date DATE
);
CREATE TABLE maintenance_logs (
    id INTEGER PRIMARY KEY,
    machine_id INTEGER,
    date DATE,
    description TEXT,
    technician_id INTEGER
);
"""

SUPPLY_SQL_MYSQL = """
CREATE TABLE products (
    id INT AUTO_INCREMENT PRIMARY KEY,
    sku VARCHAR(255),
    name TEXT,
    base_cost DECIMAL(10,2),
    category VARCHAR(100)
);
CREATE TABLE suppliers (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name TEXT,
    country VARCHAR(100)
);
CREATE TABLE inventory (
    product_id INT,
    factory_id INT,
    quantity INT,
    PRIMARY KEY (product_id, factory_id)
);
"""

SUPPLY_SQL_SQLITE = """
CREATE TABLE products (
    id INTEGER PRIMARY KEY,
    sku TEXT,
    name TEXT,
    base_cost REAL,
    category TEXT
);
CREATE TABLE suppliers (
    id INTEGER PRIMARY KEY,
    name TEXT,
    country TEXT
);
CREATE TABLE inventory (
    product_id INTEGER,
    factory_id INTEGER,
    quantity INTEGER,
    PRIMARY KEY (product_id, factory_id)
);
"""

HISTORY_SQL_MSSQL = """
CREATE TABLE sales_orders (
    id INT IDENTITY(1,1) PRIMARY KEY,
    customer_name NVARCHAR(255),
    order_date DATE,
    total_amount DECIMAL(12,2)
);
CREATE TABLE sales_items (
    id INT IDENTITY(1,1) PRIMARY KEY,
    order_id INT,
    product_id INT,
    quantity INT,
    unit_price DECIMAL(10,2)
);
CREATE TABLE production_runs (
    id INT IDENTITY(1,1) PRIMARY KEY,
    factory_id INT,
    date DATE,
    output_quantity INT
);
"""

HISTORY_SQL_SQLITE = """
CREATE TABLE sales_orders (
    id INTEGER PRIMARY KEY,
    customer_name TEXT,
    order_date DATE,
    total_amount REAL
);
CREATE TABLE sales_items (
    id INTEGER PRIMARY KEY,
    order_id INTEGER,
    product_id INTEGER,
    quantity INTEGER,
    unit_price REAL
);
CREATE TABLE production_runs (
    id INTEGER PRIMARY KEY,
    factory_id INTEGER,
    date DATE,
    output_quantity INTEGER
);
"""

DOCKER_COMPOSE_TEMPLATE = """version: '3.8'

services:
  manufacturing_ref:
    image: postgres:15
    container_name: manufacturing_ref
    ports:
      - "5433:5432"
    environment:
      POSTGRES_USER: ${DEMO_POSTGRES_USER}
      POSTGRES_PASSWORD: ${DEMO_POSTGRES_PASSWORD}
      POSTGRES_DB: manufacturing_ref
      # Env vars for Init Scripts
      DEMO_REF_USER: ${DEMO_REF_USER}
      DEMO_REF_PASSWORD: ${DEMO_REF_PASSWORD}
    volumes:
      - ./init_ref.sql:/docker-entrypoint-initdb.d/init.sql
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${DEMO_POSTGRES_USER}"]
      interval: 5s
      retries: 5

  manufacturing_ops:
    image: postgres:15
    container_name: manufacturing_ops
    ports:
      - "5434:5432"
    environment:
      POSTGRES_USER: ${DEMO_POSTGRES_USER}
      POSTGRES_PASSWORD: ${DEMO_POSTGRES_PASSWORD}
      POSTGRES_DB: manufacturing_ops
      # Env vars for Init Scripts
      DEMO_OPS_USER: ${DEMO_OPS_USER}
      DEMO_OPS_PASSWORD: ${DEMO_OPS_PASSWORD}
    volumes:
      - ./init_ops.sql:/docker-entrypoint-initdb.d/init.sql
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${DEMO_POSTGRES_USER}"]
      interval: 5s
      retries: 5

  manufacturing_supply:
    image: mysql:8
    container_name: manufacturing_supply
    ports:
      - "3307:3306"
    environment:
      MYSQL_ROOT_PASSWORD: ${DEMO_MYSQL_ROOT_PASSWORD}
      MYSQL_USER: ${DEMO_SUPPLY_USER}
      MYSQL_PASSWORD: ${DEMO_SUPPLY_PASSWORD}
      MYSQL_DATABASE: manufacturing_supply
    volumes:
      - ./init_supply.sql:/docker-entrypoint-initdb.d/init.sql
    healthcheck:
      test: ["CMD", "mysqladmin", "ping", "-h", "localhost"]
      interval: 5s
      retries: 5

  manufacturing_history:
    image: mcr.microsoft.com/mssql/server:2022-latest
    container_name: manufacturing_history
    ports:
      - "1434:1433"
    environment:
      ACCEPT_EULA: "Y"
      MSSQL_SA_PASSWORD: "${DEMO_MSSQL_SA_PASSWORD}"
      MSSQL_PID: "Express"
    user: root # Needed to run the init script wrapper if we mount it
    command: /bin/bash -c "/opt/mssql/bin/sqlservr & sleep 20 && /opt/mssql-tools/bin/sqlcmd -S localhost -U sa -P '${DEMO_MSSQL_SA_PASSWORD}' -i /init.sql -v HISTORY_USER='${DEMO_HISTORY_USER}' HISTORY_PASS='${DEMO_HISTORY_PASSWORD}' && wait"
    volumes:
      - ./init_history.sql:/init.sql
"""
