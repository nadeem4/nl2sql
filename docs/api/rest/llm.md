# LLM API

## Request/Response Models

Source: `packages/api/src/nl2sql_api/models/llm.py`

### `LLMRequest`
| field | type | required | meaning |
| --- | --- | --- | --- |
| `config` | `Dict[str, Any]` | yes | LLM config payload. |

### `LLMResponse`
| field | type | required | meaning |
| --- | --- | --- | --- |
| `success` | `bool` | yes | Operation status. |
| `message` | `str` | yes | Result message. |
| `llm_name` | `Optional[str]` | no | LLM name. |

## Endpoints

### `POST /api/v1/llm`

Execution flow:
- Delegates to `engine.configure_llm(...)`.

Errors:
- Unhandled exceptions return `HTTP 500`.

### `GET /api/v1/llm`

Response model: `Dict[str, Any]` (with `llms` map)

Execution flow:
- Delegates to `engine.list_llms()`.

### `GET /api/v1/llm/{llm_name}`

Response model: `Dict[str, Any]`

Execution flow:
- Delegates to `engine.get_llm(llm_name)`.
