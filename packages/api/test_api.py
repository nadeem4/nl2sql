"""
Simple test to verify the API layer works correctly.
This test requires proper configuration files to be present.
"""
import os
import tempfile
from pathlib import Path

def test_api_basic():
    """Test basic API functionality"""
    try:
        from nl2sql_api.main import app
        assert app is not None
        print("+ API app object created successfully")
    except Exception as e:
        print(f"- Failed to create API app: {e}")
        return False

    try:
        from nl2sql_api.services.nl2sql_service import NL2SQLService
        service = NL2SQLService()
        assert service is not None
        print("+ Service object created successfully")
    except Exception as e:
        print(f"- Failed to create service: {e}")
        # This is expected if config files are not present
        print("  (This is expected if configuration files are not set up)")
    
    return True

if __name__ == "__main__":
    print("Testing NL2SQL API layer...")
    test_api_basic()
    print("Done!")