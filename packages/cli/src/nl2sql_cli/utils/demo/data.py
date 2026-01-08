from typing import List, Dict, Any

# Shared Constants (The "Glue")

FACTORIES: List[Dict[str, Any]] = [
    {"id": 1, "name": "Austin Gigafactory", "region": "US", "capacity": 5000},
    {"id": 2, "name": "Berlin Plant", "region": "EU", "capacity": 3500},
    {"id": 3, "name": "Shanghai Facility", "region": "CN", "capacity": 8000},
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
]

SUPPLIERS: List[Dict[str, Any]] = [
    {"id": 1, "name": "Global Tech Components", "country": "Taiwan"},
    {"id": 2, "name": "SteelCorp International", "country": "Germany"},
    {"id": 3, "name": "ChemSafe Solutions", "country": "USA"},
]
