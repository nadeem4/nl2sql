# LLM Configuration

LLM configuration lives in `configs/llm.yaml` and defines the default model plus
optional per-agent overrides.

## File structure

```yaml
version: 1
default:
  provider: openai
  model: gpt-5.2
  temperature: 0.0
  api_key: ${env:OPENAI_API_KEY}
agents:
  indexing_enrichment:
    provider: openai
    model: gpt-5.2
    temperature: 0.0
    api_key: ${env:OPENAI_API_KEY}
```

## Fields

- `version`: schema version (currently `1`)
- `default`: LLM configuration used by default
- `agents`: optional map of agent name → LLM config override

Each LLM config supports:

- `provider`: LLM provider name
- `model`: model identifier
- `temperature`: float (defaults to `0.0`)
- `api_key`: optional; can use `${env:VAR}` or `${provider:key}`

## Notes

- `agents` overrides allow you to use specialized models for tasks like
  indexing enrichment while keeping a single default model for query execution.
