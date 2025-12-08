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

| Table | Rows | Description | Columns |
| :--- | :--- | :--- | :--- |
| `factories` | 3 | Manufacturing plant locations | `id`, `name`, `location`, `opened_on`, `capacity_index` |
| `machine_types` | 5 | Specifications for equipment models | `id`, `model_name`, `manufacturer`, `specifications` (JSON) |
| `shifts` | 3 | Standard work shifts (Morning, etc.) | `id`, `name`, `start_time`, `end_time` |

> **Note**: The datasource `manufacturing_sqlite` also points to this same database file but is used for "Generic" demo queries.

---

## 2. Operations Data (`manufacturing_ops`)

**Engine**: PostgreSQL  
**Description**: High-velocity operational data. Tracks daily plant activities.

| Table | Rows | Description | Columns |
| :--- | :--- | :--- | :--- |
| `employees` | 100 | Staff details and roles | `id`, `full_name`, `role`, `factory_id` (FK:Ref), `hired_date` |
| `machines` | 50 | Active equipment assets | `id`, `name`, `serial_number`, `status`, `machine_type_id` (FK:Ref) |
| `spare_parts` | 200 | inventory of repair parts | `id`, `name`, `stock_quantity`, `criticality` |
| `maintenance_logs` | 2000 | History of repairs and downtime | `id`, `machine_id`, `downtime_minutes`, `maintenance_type`, `performed_at` |

---

## 3. Supply Chain (`manufacturing_supply`)

**Engine**: MySQL  
**Description**: External-facing supply chain and inventory management.

| Table | Rows | Description | Columns |
| :--- | :--- | :--- | :--- |
| `suppliers` | 50 | Implementation vendors/partners | `id`, `name`, `country`, `rating`, `contact_email` |
| `products` | 500 | Catalog of items produced/bought | `id`, `sku`, `name`, `category`, `unit_price`, `supplier_id` |
| `inventory` | 1000 | Stock levels by warehouse | `id`, `product_id`, `warehouse_location`, `quantity_on_hand` |
| `purchase_orders` | 2000 | Procurement records | `id`, `supplier_id`, `status`, `total_amount`, `ordered_at` |
| `purchase_order_items` | ~6000 | Line items for POs | `id`, `purchase_order_id`, `product_id`, `quantity`, `unit_price` |

---

## 4. Historical Data (`manufacturing_history`)

**Engine**: Microsoft SQL Server (MSSQL)  
**Description**: Long-term archival of sales and production performance.

| Table | Rows | Description | Columns |
| :--- | :--- | :--- | :--- |
| `customers` | 200 | Client database | `id`, `company_name`, `region`, `contact_name` |
| `sales_orders` | 5000 | Validated customer orders | `id`, `customer_id`, `status`, `total_amount`, `order_date` |
| `sales_order_items` | 15,000 | Line items for Sales Orders | `id`, `sales_order_id`, `product_id`, `quantity` |
| `production_runs` | 5000 | Completed manufacturing batches | `id`, `product_id`, `quantity_produced`, `scrap_count`, `start_time` |
| `defects` | 1000 | QC failures recorded | `id`, `production_run_id`, `defect_type`, `severity`, `count` |

---

## Cross-Database Context

The system effectively joins data across these silos logically:

* **Ops** `factory_id` -> **Ref** `factories.id`
* **Supply** `product_id` <-> **History** `product_id` (Shared SKU logical link)
