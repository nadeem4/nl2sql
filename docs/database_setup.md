# Database Setup & Schema Documentation

This document outlines the database environment used in the NL2SQL pipeline, including datasources, schemas, and data volume.

## Overview

The system runs a **Multi-Database Architecture** simulating a real-world manufacturing environment.

| Metrics | Value |
| :--- | :--- |
| **Total Datasources** | 5 (4 Unique Engines) |
| **Total Tables** | 17 |
| **Total Rows** | ~30,000+ |

---

## 1. Reference Data (`manufacturing_ref`)

**Engine**: SQLite  
**File**: `data/manufacturing.db`  
**Description**: Static reference data used across the organization.

### Tables

#### `factories`

Manufacturing plant locations.

| Column | Type | Constraints |
| :--- | :--- | :--- |
| `id` | INTEGER | PRIMARY KEY |
| `name` | TEXT | NOT NULL |
| `location` | TEXT | NOT NULL |
| `opened_on` | DATE | NOT NULL |
| `capacity_index` | INTEGER | DEFAULT 100 |

#### `machine_types`

Specifications for equipment models.

| Column | Type | Constraints |
| :--- | :--- | :--- |
| `id` | INTEGER | PRIMARY KEY |
| `model_name` | TEXT | NOT NULL |
| `manufacturer` | TEXT | NOT NULL |
| `specifications` | JSON | |

#### `shifts`

Standard work shifts.

| Column | Type | Constraints |
| :--- | :--- | :--- |
| `id` | INTEGER | PRIMARY KEY |
| `name` | TEXT | NOT NULL |
| `start_time` | TIME | NOT NULL |
| `end_time` | TIME | NOT NULL |

> **Note**: The datasource `manufacturing_sqlite` also points to this same database file but is used for "Generic" demo queries.

---

## 2. Operations Data (`manufacturing_ops`)

**Engine**: PostgreSQL  
**Description**: High-velocity operational data. Tracks daily plant activities.

### Tables

#### `employees`

Staff details and roles.

| Column | Type | Constraints |
| :--- | :--- | :--- |
| `id` | SERIAL | PRIMARY KEY |
| `full_name` | TEXT | NOT NULL |
| `role` | TEXT | NOT NULL |
| `factory_id` | INTEGER | NOT NULL (Logical FK to `factories.id`) |
| `hired_date` | DATE | NOT NULL |
| `contact_info` | JSONB | |

#### `machines`

Active equipment assets.

| Column | Type | Constraints |
| :--- | :--- | :--- |
| `id` | SERIAL | PRIMARY KEY |
| `factory_id` | INTEGER | NOT NULL (Logical FK to `factories.id`) |
| `machine_type_id` | INTEGER | NOT NULL (Logical FK to `machine_types.id`) |
| `name` | TEXT | NOT NULL |
| `serial_number` | TEXT | NOT NULL UNIQUE |
| `commissioned_on` | DATE | NOT NULL |
| `status` | TEXT | NOT NULL DEFAULT 'Active' |

#### `spare_parts`

Inventory of repair parts.

| Column | Type | Constraints |
| :--- | :--- | :--- |
| `id` | SERIAL | PRIMARY KEY |
| `name` | TEXT | NOT NULL |
| `machine_type_id` | INTEGER | NOT NULL (Logical FK to `machine_types.id`) |
| `stock_quantity` | INTEGER | NOT NULL |
| `criticality` | TEXT | NOT NULL |

#### `maintenance_logs`

History of repairs and downtime.

| Column | Type | Constraints |
| :--- | :--- | :--- |
| `id` | SERIAL | PRIMARY KEY |
| `machine_id` | INTEGER | NOT NULL (FK to `machines.id`) |
| `performed_at` | TIMESTAMP | NOT NULL |
| `maintenance_type` | TEXT | NOT NULL |
| `downtime_minutes` | INTEGER | NOT NULL |
| `performed_by_id` | INTEGER | NOT NULL (FK to `employees.id`) |
| `notes` | TEXT | |

---

## 3. Supply Chain (`manufacturing_supply`)

**Engine**: MySQL  
**Description**: External-facing supply chain and inventory management.

### Tables

#### `suppliers`

Implementation vendors/partners.

| Column | Type | Constraints |
| :--- | :--- | :--- |
| `id` | INT | AUTO_INCREMENT PRIMARY KEY |
| `name` | TEXT | NOT NULL |
| `contact_email` | TEXT | NOT NULL |
| `country` | TEXT | NOT NULL |
| `rating` | FLOAT | |

#### `products`

Catalog of items produced/bought.

| Column | Type | Constraints |
| :--- | :--- | :--- |
| `id` | INT | AUTO_INCREMENT PRIMARY KEY |
| `sku` | VARCHAR(255) | NOT NULL UNIQUE |
| `name` | TEXT | NOT NULL |
| `category` | TEXT | NOT NULL |
| `unit_price` | DECIMAL(10, 2) | NOT NULL |
| `supplier_id` | INT | NOT NULL (FK to `suppliers.id`) |
| `description` | TEXT | |

#### `inventory`

Stock levels by warehouse.

| Column | Type | Constraints |
| :--- | :--- | :--- |
| `id` | INT | AUTO_INCREMENT PRIMARY KEY |
| `product_id` | INT | NOT NULL (FK to `products.id`) |
| `warehouse_location` | TEXT | NOT NULL |
| `quantity_on_hand` | INT | NOT NULL |
| `last_updated` | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP |

#### `purchase_orders`

Procurement records.

| Column | Type | Constraints |
| :--- | :--- | :--- |
| `id` | INT | AUTO_INCREMENT PRIMARY KEY |
| `supplier_id` | INT | NOT NULL (FK to `suppliers.id`) |
| `ordered_at` | DATETIME | NOT NULL |
| `status` | VARCHAR(50) | NOT NULL |
| `total_amount` | DECIMAL(12, 2) | NOT NULL |

#### `purchase_order_items`

Line items for POs.

| Column | Type | Constraints |
| :--- | :--- | :--- |
| `id` | INT | AUTO_INCREMENT PRIMARY KEY |
| `purchase_order_id` | INT | NOT NULL (FK to `purchase_orders.id`) |
| `product_id` | INT | NOT NULL (FK to `products.id`) |
| `quantity` | INT | NOT NULL |
| `unit_price` | DECIMAL(10, 2) | NOT NULL |

---

## 4. Historical Data (`manufacturing_history`)

**Engine**: Microsoft SQL Server (MSSQL)  
**Description**: Long-term archival of sales and production performance.

### Tables

#### `customers`

Client database.

| Column | Type | Constraints |
| :--- | :--- | :--- |
| `id` | INT | IDENTITY(1,1) PRIMARY KEY |
| `company_name` | NVARCHAR(255) | NOT NULL |
| `contact_name` | NVARCHAR(255) | NOT NULL |
| `email` | NVARCHAR(255) | NOT NULL |
| `region` | NVARCHAR(100) | NOT NULL |

#### `production_runs`

Completed manufacturing batches.

| Column | Type | Constraints |
| :--- | :--- | :--- |
| `id` | INT | IDENTITY(1,1) PRIMARY KEY |
| `product_id` | INT | NOT NULL (Logical FK) |
| `machine_id` | INT | NOT NULL (Logical FK) |
| `start_time` | DATETIME | NOT NULL |
| `end_time` | DATETIME | NOT NULL |
| `quantity_produced` | INT | NOT NULL |
| `scrap_count` | INT | NOT NULL DEFAULT 0 |

#### `defects`

QC failures recorded.

| Column | Type | Constraints |
| :--- | :--- | :--- |
| `id` | INT | IDENTITY(1,1) PRIMARY KEY |
| `production_run_id` | INT | NOT NULL (FK to `production_runs.id`) |
| `defect_type` | NVARCHAR(100) | NOT NULL |
| `severity` | NVARCHAR(50) | NOT NULL |
| `count` | INT | NOT NULL |

#### `sales_orders`

Validated customer orders.

| Column | Type | Constraints |
| :--- | :--- | :--- |
| `id` | INT | IDENTITY(1,1) PRIMARY KEY |
| `customer_id` | INT | NOT NULL (FK to `customers.id`) |
| `order_date` | DATETIME | NOT NULL |
| `status` | NVARCHAR(50) | NOT NULL |
| `total_amount` | DECIMAL(12, 2) | NOT NULL |

#### `sales_order_items`

Line items for Sales Orders.

| Column | Type | Constraints |
| :--- | :--- | :--- |
| `id` | INT | IDENTITY(1,1) PRIMARY KEY |
| `sales_order_id` | INT | NOT NULL (FK to `sales_orders.id`) |
| `product_id` | INT | NOT NULL (Logical FK) |
| `quantity` | INT | NOT NULL |
| `unit_price` | DECIMAL(10, 2) | NOT NULL |

---

## Cross-Database Context

The system effectively joins data across these silos logically:

* **Ops** `factory_id` -> **Ref** `factories.id`
* **Supply** `product_id` <-> **History** `product_id` (Shared SKU logical link)
