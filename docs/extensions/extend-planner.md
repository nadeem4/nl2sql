# Extending the Planner

The planner generates a structured AST (`PlanModel`) which is validated and compiled into SQL. Extending planner logic typically requires changes to schema models, prompts, validator, and generator.

## 1. Update AST schemas

Modify `PlanModel` or related AST models in:

- `packages/core/src/nl2sql/pipeline/nodes/ast_planner/schemas.py`

Keep these rules in mind:

- Models are strict (`extra="forbid"`).
- Ordinals must be contiguous (validated by the logical validator).
- Expression kinds must pass `model_post_init` validation.

## 2. Update planner prompt

Update `PLANNER_PROMPT` and examples in:

- `packages/core/src/nl2sql/pipeline/nodes/ast_planner/prompts.py`

Ensure structured output still matches `PlanModel`.

## 3. Update validator logic

The logical validator enforces:

- query type (READ-only)
- table/column existence
- alias uniqueness
- join relationships
- expected schema alignment

Update `LogicalValidatorNode` to recognize any new AST constructs.

## 4. Update SQL generator

`GeneratorNode` and `SqlVisitor` translate the AST into `sqlglot` expressions:

- Extend `SqlVisitor` to handle new expression kinds or operators.
- Update `GeneratorNode._generate_sql()` if new clauses are added.

## Source references

- AST schemas: `packages/core/src/nl2sql/pipeline/nodes/ast_planner/schemas.py`
- Planner node: `packages/core/src/nl2sql/pipeline/nodes/ast_planner/node.py`
- Logical validator: `packages/core/src/nl2sql/pipeline/nodes/validator/node.py`
- SQL generator: `packages/core/src/nl2sql/pipeline/nodes/generator/node.py`
