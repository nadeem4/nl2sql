#!/usr/bin/env python
"""
NL2SQL API Startup Script

This script starts the NL2SQL API server.
"""

import argparse
import sys
from nl2sql_api.main import app
import uvicorn

def main():
    parser = argparse.ArgumentParser(description='NL2SQL API Server')
    parser.add_argument('--host', default='127.0.0.1', help='Host to bind to (default: 127.0.0.1)')
    parser.add_argument('--port', type=int, default=8000, help='Port to bind to (default: 8000)')
    parser.add_argument('--reload', action='store_true', help='Enable auto-reload (development)')
    
    args = parser.parse_args()
    
    uvicorn.run(
        'nl2sql_api.main:app',
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info"
    )

if __name__ == '__main__':
    main()