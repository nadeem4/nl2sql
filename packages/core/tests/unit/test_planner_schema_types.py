import pytest
from pydantic import ValidationError
from nl2sql.pipeline.nodes.planner.schemas import Expr, CaseWhen

def test_expr_literal_validation():
    """Test strict validation for Literal kind."""
    # Valid
    Expr(kind="literal", value=1)
    Expr(kind="literal", value="test")
    Expr(kind="literal", value=True)
    Expr(kind="literal", is_null=True)

    # Invalid: Missing value and is_null=False
    with pytest.raises(ValidationError) as exc:
        Expr(kind="literal")
    assert "Literal must have value or is_null=True" in str(exc.value)

def test_expr_column_validation():
    """Test strict validation for Column kind."""
    # Valid
    Expr(kind="column", column_name="id", table="users")
    
    # Invalid: Missing column_name
    with pytest.raises(ValidationError) as exc:
        Expr(kind="column", table="users")
    assert "column_name is required" in str(exc.value)

def test_expr_func_validation():
    """Test strict validation for Func kind."""
    # Valid
    Expr(kind="func", func_name="COUNT", args=[Expr(kind="column", column_name="id")])
    
    # Invalid: Missing func_name
    with pytest.raises(ValidationError) as exc:
        Expr(kind="func", args=[])
    assert "func_name is required" in str(exc.value)

def test_expr_binary_validation():
    """Test strict validation for Binary kind."""
    # Valid
    Expr(
        kind="binary", 
        op="=", 
        left=Expr(kind="literal", value=1), 
        right=Expr(kind="literal", value=1)
    )

    # Invalid: Missing op
    with pytest.raises(ValidationError) as exc:
        Expr(
            kind="binary", 
            left=Expr(kind="literal", value=1), 
            right=Expr(kind="literal", value=1)
        )
    assert "Binary expression requires operator" in str(exc.value)

    # Invalid: Missing operands
    with pytest.raises(ValidationError) as exc:
        Expr(kind="binary", op="=")
    assert "Binary expression requires left and right" in str(exc.value)

def test_expr_unary_validation():
    """Test strict validation for Unary kind."""
    # Valid
    Expr(kind="unary", op="NOT", expr=Expr(kind="literal", value=True))
    
    # Invalid: Bad operator
    with pytest.raises(ValidationError):
        Expr(kind="unary", op="INVALID", expr=Expr(kind="literal", value=True))

def test_case_when_validation():
    """Test strict validation for Case kind."""
    when = CaseWhen(
        condition=Expr(kind="literal", value=True),
        result=Expr(kind="literal", value="yes"),
        ordinal=0
    )
    # Valid
    Expr(kind="case", whens=[when])
    
    # Invalid: Empty whens
    with pytest.raises(ValidationError) as exc:
        Expr(kind="case", whens=[])
    assert "CASE expression must have at least one WHEN" in str(exc.value)
