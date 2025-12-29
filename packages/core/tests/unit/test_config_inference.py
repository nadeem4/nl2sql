import unittest
from nl2sql.datasources.config import _to_profile

class TestConfigInference(unittest.TestCase):
    def test_infer_postgres(self):
        raw = {
            "id": "test_pg",
            "sqlalchemy_url": "postgresql+psycopg2://user:pass@localhost/db"
        }
        profile = _to_profile(raw)
        self.assertEqual(profile.engine, "postgres")

    def test_infer_sqlite(self):
        raw = {
            "id": "test_sqlite",
            "sqlalchemy_url": "sqlite:///data.db"
        }
        profile = _to_profile(raw)
        self.assertEqual(profile.engine, "sqlite")

    def test_infer_unknown(self):
        raw = {
            "id": "test_other",
            "sqlalchemy_url": "snowflake://user:pass@account/db"
        }
        profile = _to_profile(raw)
        # SQLAlchemy make_url should return 'snowflake', but our map doesn't normalize it, so it should be 'snowflake'
        self.assertEqual(profile.engine, "snowflake")
    
    def test_infer_explicit_override(self):
        # Even if URL says postgres, if I say 'custom', it should be 'custom'
        raw = {
            "id": "test_override",
            "sqlalchemy_url": "postgresql://localhost",
            "engine": "custom_engine"
        }
        profile = _to_profile(raw)
        self.assertEqual(profile.engine, "custom_engine")

    def test_explicit_normalization(self):
        # If user explicitly says 'postgresql' (alias), it should become 'postgres'
        raw = {
            "id": "test_alias",
            "sqlalchemy_url": "postgresql://localhost",
            "engine": "postgresql" 
        }
        profile = _to_profile(raw)
        self.assertEqual(profile.engine, "postgres")

if __name__ == "__main__":
    unittest.main()
