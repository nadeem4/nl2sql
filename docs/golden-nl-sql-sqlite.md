# Golden NL → SQL pairs (SQLite, manufacturing)

- NL: "Show total quantity produced and scrap for each product in the last 2 days."
  SQL:
  ```sql
  SELECT
    p.sku,
    p.name,
    SUM(r.quantity_produced) AS total_qty,
    SUM(r.scrap_count) AS total_scrap
  FROM production_runs r
  JOIN products p ON p.id = r.product_id
  WHERE r.started_at >= datetime('now', '-2 days')
  GROUP BY p.sku, p.name
  ORDER BY total_qty DESC;
  ```

- NL: "List defects by type and severity for Widget Alpha."
  SQL:
  ```sql
  SELECT
    d.defect_type,
    d.severity,
    SUM(d.defect_count) AS total_defects
  FROM defects d
  JOIN production_runs r ON r.id = d.production_run_id
  JOIN products p ON p.id = r.product_id
  WHERE p.name = 'Widget Alpha'
  GROUP BY d.defect_type, d.severity
  ORDER BY total_defects DESC;
  ```

- NL: "Show maintenance downtime per machine over the last week."
  SQL:
  ```sql
  SELECT
    m.name AS machine,
    SUM(l.downtime_minutes) AS downtime_minutes
  FROM maintenance_logs l
  JOIN machines m ON m.id = l.machine_id
  WHERE l.performed_at >= datetime('now', '-7 days')
  GROUP BY m.name
  ORDER BY downtime_minutes DESC;
  ```

- NL: "What is the current on-hand inventory per product and warehouse?"
  SQL:
  ```sql
  SELECT
    p.sku,
    p.name,
    i.warehouse,
    i.on_hand,
    i.updated_at
  FROM inventory i
  JOIN products p ON p.id = i.product_id
  ORDER BY p.sku, i.warehouse;
  ```

- NL: "Show total quantity and scrap per factory for Widgets products."
  SQL:
  ```sql
  SELECT
    f.name AS factory,
    SUM(r.quantity_produced) AS total_qty,
    SUM(r.scrap_count) AS total_scrap
  FROM production_runs r
  JOIN machines m ON m.id = r.machine_id
  JOIN factories f ON f.id = m.factory_id
  JOIN products p ON p.id = r.product_id
  WHERE p.category = 'Widgets'
  GROUP BY f.name
  HAVING SUM(r.quantity_produced) > 0
  ORDER BY total_qty DESC;
  ```

- NL: "List maintenance events with downtime over 60 minutes."
  SQL:
  ```sql
  SELECT
    m.name AS machine,
    l.maintenance_type,
    l.downtime_minutes,
    l.performed_at
  FROM maintenance_logs l
  JOIN machines m ON m.id = l.machine_id
  WHERE l.downtime_minutes > 60
  ORDER BY l.downtime_minutes DESC;
  ```

- NL: "Top defects by count for each product."
  SQL:
  ```sql
  SELECT
    p.sku,
    p.name,
    d.defect_type,
    SUM(d.defect_count) AS total_defects
  FROM defects d
  JOIN production_runs r ON r.id = d.production_run_id
  JOIN products p ON p.id = r.product_id
  GROUP BY p.sku, p.name, d.defect_type
  HAVING SUM(d.defect_count) > 0
  ORDER BY total_defects DESC;
  ```

- NL: "Show production runs on or after 2024-04-09 for Widgets, ordered by start time."
  SQL:
  ```sql
  SELECT
    r.id,
    p.sku,
    p.name,
    r.started_at,
    r.ended_at,
    r.quantity_produced,
    r.scrap_count
  FROM production_runs r
  JOIN products p ON p.id = r.product_id
  WHERE p.category = 'Widgets'
    AND r.started_at >= '2024-04-09T00:00:00'
  ORDER BY r.started_at ASC
  LIMIT 50;
  ```

- NL: "Find machines commissioned after 2013 with maintenance over 60 minutes."
  SQL:
  ```sql
  SELECT
    m.name,
    m.line,
    m.commissioned_on,
    l.maintenance_type,
    l.downtime_minutes
  FROM machines m
  JOIN maintenance_logs l ON l.machine_id = m.id
  WHERE m.commissioned_on > '2013-01-01'
    AND l.downtime_minutes > 60
  ORDER BY l.downtime_minutes DESC;
  ```

- NL: "Inventory positions updated after 2024-04-09 by warehouse."
  SQL:
  ```sql
  SELECT
    i.warehouse,
    p.sku,
    p.name,
    i.on_hand,
    i.updated_at
  FROM inventory i
  JOIN products p ON p.id = i.product_id
  WHERE i.updated_at > '2024-04-09T00:00:00'
  ORDER BY i.warehouse, p.sku;
  ```
