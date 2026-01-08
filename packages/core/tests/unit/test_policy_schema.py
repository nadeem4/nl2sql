import pytest
from pydantic import ValidationError
from nl2sql.security.policies import RolePolicy, PolicyConfig

def test_valid_policy_exact_match():
    policy = RolePolicy(
        description="Test",
        role="test",
        allowed_tables=["db.table"]
    )
    assert policy.allowed_tables == ["db.table"]

def test_valid_policy_wildcard():
    policy = RolePolicy(
        description="Test",
        role="test",
        allowed_tables=["db.*"]
    )
    assert policy.allowed_tables == ["db.*"]

def test_valid_policy_global_wildcard():
    policy = RolePolicy(
        description="Test",
        role="test",
        allowed_tables=["*"]
    )
    assert policy.allowed_tables == ["*"]

def test_invalid_simple_table_name():
    with pytest.raises(ValidationError) as exc:
        RolePolicy(
            description="Test",
            role="test",
            allowed_tables=["orders"]  # Missing namescape
        )
    assert "Invalid table 'orders'" in str(exc.value)
    assert "datasource.table" in str(exc.value)

 

def test_invalid_wildcard_no_dot():
    with pytest.raises(ValidationError) as exc:
        RolePolicy(
            description="Test",
            role="test",
            allowed_tables=["*orders"] 
        )
    assert "Invalid table" in str(exc.value)

def test_policy_config_parsing():
    json_data = """
    {
        "admin": {
            "description": "Admin",
            "role": "admin",
            "allowed_datasources": ["*"],
            "allowed_tables": ["*"]
        }
    }
    """
    config = PolicyConfig.model_validate_json(json_data)
    assert config.get_role("admin").role == "admin"
