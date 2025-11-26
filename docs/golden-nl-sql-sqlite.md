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
