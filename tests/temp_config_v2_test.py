
import os
import pathlib
import pytest
from nl2sql.datasources.config import load_profiles, DatasourceProfile

CONFIG_CONTENT_V2 = """
- id: test_v2_db
  type: postgres
  connection:
    host: localhost
    port: 5432
    user: admin
    password: "${DB_PASSWORD}"
    database: analytics
"""

def test_config_loading_v2(tmp_path):
    # Setup
    config_file = tmp_path / "datasources_test.yaml"
    config_file.write_text(CONFIG_CONTENT_V2, encoding="utf-8")
    
    # Set Env Var
    os.environ["DB_PASSWORD"] = "super_secret_123"
    
    try:
        # Action
        profiles = load_profiles(config_file)
        profile = profiles["test_v2_db"]
        
        # Assertion 1: Schema Conversion (Connection Model)
        # Should build: postgresql://admin:super_secret_123@localhost:5432/analytics
        assert profile.type == "postgres"
        assert profile.connection is not None
        # Pydantic Model access
        assert profile.connection.password == "super_secret_123"
        
        # Check UrlBuilder behavior separately (integration check)
        from nl2sql.datasources.url_builder import UrlBuilder
        # UrlBuilder handles Pydantic models via model_dump()
        url_str = UrlBuilder.build(profile.type, profile.connection)
        assert "postgresql://admin:super_secret_123@localhost:5432/analytics" in url_str
        
        # Assertion 2: Secret Masking
        # repr() should NOT show the password
        repr_str = repr(profile)
        assert "super_secret_123" not in repr_str
        assert "connection=***" in repr_str
        
        print("\nSUCCESS: All V2 Config Tests Passed!")
        
    finally:
        del os.environ["DB_PASSWORD"]

if __name__ == "__main__":
    # Minimal runner if pytest isn't handy
    import sys
    try:
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            test_config_loading_v2(pathlib.Path(td))
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"FAILED: {e}")
        sys.exit(1)
