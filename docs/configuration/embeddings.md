# Embeddings

The `embeddings` section of [`nl2sql.yaml`](system.md) chooses how schema
entries are embedded during `nl2sql index`, and how questions are embedded
during retrieval. `nl2sql init` writes the key-free local provider:

```yaml
# embeddings.yaml
provider: local
```

| Field | Default | Meaning |
| --- | --- | --- |
| `provider` | required | `local`, `openai`, or a provider a plugin registers. |
| `model` | the provider's default | The embedding model. |
| `api_key` | the provider's standard variable | A `${env:VAR}` or `${provider:key}` reference, resolved when the embedder is built. |

In Python, `Embeddings(instance=MyEmbeddings())` passes a LangChain embeddings
object instead of a provider name. Each engine builds its own embedder once.

## Providers

| `provider` | Model | Dimensions | API key |
| --- | --- | --- | --- |
| `local` | `all-MiniLM-L6-v2` (ONNX, bundled with chromadb); the only model | 384 | none |
| `openai` | `model`, default `text-embedding-3-small` | 1536 | `api_key`, else `OPENAI_API_KEY` |

An `openai` section with no key resolving is a `ConfigError` when the engine
is built, naming the variable.

Notes on the `local` provider:

- **No extra dependency.** The model ships with `chromadb`, which is already a
  transitive dependency through `langchain-chroma`.
- **One-time download.** The first indexing run downloads roughly 79 MB of ONNX
  model files into a local cache directory. Expect a pause of up to a few
  minutes; a log line is emitted before the download starts.
- **Embeddings only.** This removes the API key requirement from the embedding
  step. It does not make querying key-free: answering a question still needs a
  chat model (see [LLMs](llm.md)).

## Switching providers

**Switching providers requires a re-index.** The two providers produce vectors
of different dimensionality, so an existing index cannot be read with the
other provider. Retrieving from an index whose vectors disagree with the
configured provider raises `EmbeddingDimensionMismatchError` naming both
providers. The check runs on the read path only, so the fix is to re-run
`nl2sql index --full`; alternatively, point [`storage.path`](storage.md) at a
separate folder per provider.
