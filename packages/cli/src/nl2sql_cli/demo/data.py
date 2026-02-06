from typing import List, Dict, Any

# Shared Constants (The "Glue")

FACTORIES: List[Dict[str, Any]] = [
    {"id": 1, "name": "Austin Gigafactory", "region": "US", "capacity": 5000},
    {"id": 2, "name": "Berlin Plant", "region": "EU", "capacity": 3500},
    {"id": 3, "name": "Shanghai Facility", "region": "CN", "capacity": 8000},
    {"id": 4, "name": "Tokyo Hub", "region": "JP", "capacity": 4200},
    {"id": 5, "name": "Mumbai Plant", "region": "IN", "capacity": 3000},
]

SHIFTS: List[Dict[str, Any]] = [
    {"id": 1, "name": "Morning", "start_time": "06:00", "end_time": "14:00"},
    {"id": 2, "name": "Evening", "start_time": "14:00", "end_time": "22:00"},
    {"id": 3, "name": "Night", "start_time": "22:00", "end_time": "06:00"},
]

MACHINE_TYPES: List[Dict[str, Any]] = [
    {"id": 1, "model": "Robotic Arm v1", "producer": "TechCorp", "maintenance_interval_days": 30},
    {"id": 2, "model": "Conveyor Belt Gen3", "producer": "MoveIt", "maintenance_interval_days": 180},
    {"id": 3, "model": "Stamping Press 500T", "producer": "HeavyMetal", "maintenance_interval_days": 90},
    {"id": 4, "model": "Painting Station", "producer": "ColorsInc", "maintenance_interval_days": 14},
    {"id": 5, "model": "Quality Scanner", "producer": "VisionAI", "maintenance_interval_days": 60},
    {"id": 6, "model": "Laser Cutter L2", "producer": "PhotonWorks", "maintenance_interval_days": 45},
    {"id": 7, "model": "CNC Mill Pro", "producer": "Machina", "maintenance_interval_days": 120},
]

PRODUCTS: List[Dict[str, Any]] = [
    # High Value
    {"id": 1, "sku": "IC-5000", "name": "Industrial Controller", "base_cost": 4500.00, "category": "Electronics"},
    {"id": 2, "sku": "BAT-EV-X", "name": "EV Battery Pack Long Range", "base_cost": 8000.00, "category": "Components"},
    {"id": 3, "sku": "MOT-HI-T", "name": "High Torque Motor", "base_cost": 1200.00, "category": "Components"},
    # High Volume
    {"id": 4, "sku": "BOLT-M5", "name": "Bolt M5 Stainless", "base_cost": 0.50, "category": "Hardware"},
    {"id": 5, "sku": "WASH-M5", "name": "Washer M5", "base_cost": 0.10, "category": "Hardware"},
    {"id": 6, "sku": "CAB-USB-C", "name": "USB-C Data Cable", "base_cost": 2.50, "category": "Electronics"},
    {"id": 7, "sku": "BRK-SET", "name": "Brake Assembly Set", "base_cost": 350.00, "category": "Components"},
    {"id": 8, "sku": "SNS-TEMP", "name": "Temperature Sensor Pack", "base_cost": 85.00, "category": "Electronics"},
    {"id": 9, "sku": "PNL-AL", "name": "Aluminum Panel Sheet", "base_cost": 40.00, "category": "Materials"},
]

SUPPLIERS: List[Dict[str, Any]] = [
    {"id": 1, "name": "Global Tech Components", "country": "Taiwan"},
    {"id": 2, "name": "SteelCorp International", "country": "Germany"},
    {"id": 3, "name": "ChemSafe Solutions", "country": "USA"},
    {"id": 4, "name": "Nippon Metals", "country": "Japan"},
    {"id": 5, "name": "Indus Industrial", "country": "India"},
]

DEPARTMENTS: List[Dict[str, Any]] = [
    {"id": 1, "name": "Assembly"},
    {"id": 2, "name": "Quality Assurance"},
    {"id": 3, "name": "Logistics"},
    {"id": 4, "name": "Maintenance"},
    {"id": 5, "name": "Engineering"},
]

CUSTOMER_SEGMENTS: List[Dict[str, Any]] = [
    {"id": 1, "name": "Enterprise"},
    {"id": 2, "name": "SMB"},
    {"id": 3, "name": "Retail"},
]

EMPLOYEE_ROLES: List[Dict[str, Any]] = [
    {"id": 1, "title": "Operator", "department_id": 1},
    {"id": 2, "title": "Line Supervisor", "department_id": 1},
    {"id": 3, "title": "QA Analyst", "department_id": 2},
    {"id": 4, "title": "Logistics Coordinator", "department_id": 3},
    {"id": 5, "title": "Maintenance Technician", "department_id": 4},
    {"id": 6, "title": "Process Engineer", "department_id": 5},
]

SUPPLIER_PRODUCTS: List[Dict[str, Any]] = [
    {"supplier_id": 1, "product_id": 1},
    {"supplier_id": 1, "product_id": 2},
    {"supplier_id": 1, "product_id": 8},
    {"supplier_id": 2, "product_id": 4},
    {"supplier_id": 2, "product_id": 5},
    {"supplier_id": 2, "product_id": 9},
    {"supplier_id": 3, "product_id": 6},
    {"supplier_id": 3, "product_id": 7},
    {"supplier_id": 4, "product_id": 3},
    {"supplier_id": 4, "product_id": 9},
    {"supplier_id": 5, "product_id": 7},
    {"supplier_id": 5, "product_id": 4},
]
