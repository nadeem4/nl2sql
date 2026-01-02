# Deterministic SQL Generation Architecture

**Status**: Adopted
**Date**: 2026-01-01

## 1. Overview

This document details the architectural shift from a "Linear Plan" to a **Recursive Abstract Syntax Tree (AST)** for SQL generation. The goal is to maximize **determinism**, **type safety**, and **expressiveness** (handling nested logic like `(A OR B) AND C`).

### Core Concepts

* **Recursive AST**: The Plan is no longer a flat list of filters. It is a tree of `Expr` nodes (BinaryOp, Func, Literal, etc.).
* **Visitor Pattern**: The `GeneratorNode` and `ValidatorNode` implement a `visit(expr)` method to traverse this tree.
* **Explicit Ordinality**: All JSON lists (`tables`, `select_items`) must be sorted by an explicit `ordinal` field before processing to guarantee stable SQL output.
* **Strict Typing**: We use `Pydantic` with `extra="forbid"` to fail fast on any LLM hallucinations.

---

## 2. The Recursive AST Schema

The core of this architecture is the `Expr` union type, defined in `nl2sql.pipeline.nodes.planner.schemas`.

### 1.1 Unified `Expr` Schema

To ensure compatibility with LLM Structured Output APIs (which often reject complex `oneOf` polymorphism), we use a **Single Unified `Expr` Class**.

```python
class Expr(BaseModel):
    kind: Literal["literal", "column", "func", "binary", "unary", "case"]

    # Valid fields depend on 'kind'
    value: Optional[Union[str, int, float, bool]] = None
    is_null: bool = False

    table: Optional[str] = None
    column_name: Optional[str] = None  # Explicit named field for columns

    func_name: Optional[str] = None    # Explicit named field for functions
    args: List["Expr"] = []
    is_aggregate: bool = False

    op: Optional[Literal["=", "!=", ">", ...]] = None
    left: Optional["Expr"] = None
    right: Optional["Expr"] = None

    expr: Optional["Expr"] = None      # for unary

    whens: List[CaseWhen] = []         # for case
    else_expr: Optional["Expr"] = None
```

This flattening removes `Union` types while preserving the recursive logical structure via `left/right` fields.

### 2.2. The Plan Model

The `PlanModel` acts as the root of the AST, mimicking a standard SQL `SELECT` statement structure.

```python
class PlanModel(BaseModel):
    query_type: Literal["READ"] = "READ"
    tables: List[TableRef]      # Definitions (Name + Alias) + Ordinal
    joins: List[JoinSpec]       # Relations + Ordinal
    
    select_items: List[SelectItem] # Projections + Ordinal
    where: Optional[Expr]          # Nested Logic Tree
    group_by: List[GroupByItem]    # Groupings + Ordinal
    having: Optional[Expr]         # Nested Logic Tree
    order_by: List[OrderItem]      # Sort + Ordinal
    
    limit: Optional[int]
```

---

## 3. The Visitor Pattern (Generator Strategy)

The `GeneratorNode` is responsible for compiling this AST into a `sqlglot` object tree. It explicitly **does not** parse strings.

### 3.1. `SqlVisitor` Class

We will implement a `SqlVisitor` class that dispatches based on `kind`.

```python
class SqlVisitor:
    def visit(self, expr: Expr) -> sqlglot.exp.Expression:
        method_name = f"visit_{expr.kind}"
        visitor = getattr(self, method_name, self.generic_visit)
        return visitor(expr)

    def visit_binary(self, expr: BinaryOpExpr) -> sqlglot.exp.Expression:
        left = self.visit(expr.left)
        right = self.visit(expr.right)
        
        # Explicit mapping for determinism
        if expr.op == "=": return sqlglot.exp.EQ(this=left, expression=right)
        if expr.op == "AND": return sqlglot.exp.And(this=left, expression=right)
        # ... mappings for all operators
        
    def visit_literal(self, expr: LiteralExpr) -> sqlglot.exp.Expression:
        # Handles strict type conversion
        if expr.value is None: return sqlglot.exp.Null()
        return sqlglot.exp.Literal.number(expr.value) if isinstance(expr.value, (int, float)) else ...
```

---

## 4. Determinism Guarantees

We enforce reproducible outputs through three mechanisms:

1. **Sorting Protocol**:
    * Before any processing, the Generator **MUST** sort `tables`, `joins`, `select_items`, `group_by`, and `order_by` using the `ordinal` field.
    * `lists.sort(key=lambda x: x.ordinal)`
    * This renders the JSON array order irrelevant.

2. **No "Extra" Fields**:
    * `model_config = ConfigDict(extra="forbid")` ensures the AST contains *only* what is defined. No hidden prompts or hints can sneak in.

3. **No String Parsing**:
    * Values like dates or complex strings are handled as typed `LiteralExpr` objects, not substrings. This prevents injection attacks and formatting errors.

---

## 5. Validator Adaptation

The `ValidatorNode` will also adopt a Visitor approach for Static Analysis.

* **`ValidatorVisitor`**: Recursively traverses `Expr` nodes.
* **Checks**:
  * `visit_column`: Verifies `table.column` exists in the Schema.
  * `visit_func`: Verifies function name is allowed (whitelist).
  * `visit_binary`: Verifies type compatibility (e.g., `date > number` warning).
