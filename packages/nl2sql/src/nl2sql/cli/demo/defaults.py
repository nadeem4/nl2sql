
# Default Configurations for Demo

SAMPLE_QUESTIONS = {
    "manufacturing_ref": [
        "List all factories in the US",
        "Show me the capacity of Berlin Plant",
        "What shifts are available?",
        "List all machine types produced by TechCorp",
        "Which factories have capacity greater than 4000?",
        "Show all employee roles in the Maintenance department",
        "List customer segments available for reporting"
    ],
    "manufacturing_ops": [
        "Show me active employees in the Austin Gigafactory",
        "Which machines have error logs in the last 7 days?",
        "Who is the operator for machine 5?",
        "Count the number of active machines per factory",
        "List maintenance logs for Vibration sensor alerts",
        "Which machines are overdue for maintenance based on last_maintenance_date?",
        "Show employees hired in the last 12 months",
        "Compare machine status counts by factory",
        "List maintenance technicians with the most downtime hours logged"
    ],
    "manufacturing_supply": [
        "Total sales amount for 'Industrial Controller'",
        "Find suppliers for high value components",
        "Check inventory levels for 'Bolt M5' in Berlin",
        "List products with base cost greater than 500",
        "Show me suppliers from Germany",
        "List products with low inventory across all factories",
        "Which suppliers provide EV Battery Pack Long Range?",
        "Show inventory last updated more than 7 days ago",
        "Compare inventory levels for Hardware category products"
    ],
    "manufacturing_history": [
        "Show total sales orders in Q4",
        "Calculate average production output per run",
        "Summarize sales by customer for last year",
        "List the top 5 largest orders",
        "Show sales orders by status for the last 30 days",
        "Compare production output by factory over the last quarter",
        "List highest revenue products by month",
        "Show average discount percentage by customer segment"
    ]
}

DEMO_POLICIES = {
    "admin": {
        "description": "Demo Admin",
        "role": "admin",
        "allowed_datasources": ["*"],
        "allowed_tables": ["*"]
    }
}

DEMO_LLM_CONFIG = {
    "default": {
        "provider": "openai",
        "model": "gpt-4o",
        "api_key": "${env:OPENAI_API_KEY}"
    }
}

DEMO_LITE_DATASOURCES = [
    {"id": "manufacturing_ref", "connection": {"type": "sqlite", "database": "data/demo_lite/manufacturing_ref.db"}, "description": "Master Data (Factories)"},
    {"id": "manufacturing_ops", "connection": {"type": "sqlite", "database": "data/demo_lite/manufacturing_ops.db"}, "description": "Operational Data (Employees, Machines)"},
    {"id": "manufacturing_supply", "connection": {"type": "sqlite", "database": "data/demo_lite/manufacturing_supply.db"}, "description": "Supply Chain (Inventory)"},
    {"id": "manufacturing_history", "connection": {"type": "sqlite", "database": "data/demo_lite/manufacturing_history.db"}, "description": "Historical Data (Sales)"},
]

DEMO_DOCKER_DATASOURCES = [
    {
        "id": "manufacturing_ref", 
        "connection": {
            "type": "postgres", 
            "host": "localhost",
            "port": 5433,
            "user": "${env:DEMO_REF_USER}",
            "password": "${env:DEMO_REF_PASSWORD}",
            "database": "manufacturing_ref"
        }, 
        "description": "Master Data (Factories)"
    },
    {
        "id": "manufacturing_ops", 
        "connection": {
            "type": "postgres", 
            "host": "localhost",
            "port": 5434,
            "user": "${env:DEMO_OPS_USER}",
            "password": "${env:DEMO_OPS_PASSWORD}",
            "database": "manufacturing_ops"
        }, 
        "description": "Operational Data (Employees, Machines)"
    },
    {
        "id": "manufacturing_supply", 
        "connection": {
            "type": "mysql", 
            "host": "localhost",
            "port": 3307,
            "user": "${env:DEMO_SUPPLY_USER}",
            "password": "${env:DEMO_SUPPLY_PASSWORD}",
            "database": "manufacturing_supply"
        }, 
        "description": "Supply Chain (Inventory)"
    },
    {
        "id": "manufacturing_history", 
        "connection": {
            "type": "mssql", 
            "host": "localhost",
            "port": 1434,
            "user": "${env:DEMO_HISTORY_USER}",
            "password": "${env:DEMO_HISTORY_PASSWORD}",
            "database": "manufacturing_history",
            "driver": "ODBC Driver 17 for SQL Server"
        }, 
        "description": "Historical Data (Sales)"
    },
]
