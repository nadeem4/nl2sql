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
| `provider` | required | `local`, `openai`, `openai_compatible`, or a provider a plugin registers. |
| `model` | the provider's default | The embedding model. Required for `openai_compatible`. |
| `api_key` | the provider's standard variable | A `${env:VAR}` or `${provider:key}` reference, resolved when the embedder is built. |
| `base_url` | none | The server's URL, for `openai` and `openai_compatible`. An error for `local`. |
| `dimension` | the provider's | The model's output dimension. Write it only when the provider does not know it and you want to skip the probe (see [Model and dimension](#model-and-dimension)). |

In Python, `Embeddings(instance=MyEmbeddings())` passes a LangChain embeddings
object instead of a provider name. Each engine builds its own embedder once.

## Providers

| `provider` | Model | Dimension | API key | `base_url` |
| --- | --- | --- | --- | --- |
| `local` | `all-MiniLM-L6-v2` (ONNX, bundled with chromadb); the only model | 384 | none | not allowed |
| `openai` | `model`, default `text-embedding-3-small` | known for OpenAI's own models (1536, or 3072 for `text-embedding-3-large`) | `api_key`, else `OPENAI_API_KEY` | optional |
| `openai_compatible` | `model`, required | `dimension:`, else probed | `api_key` if the server needs one | required |

An `openai` section with no key resolving, an `openai_compatible` section
without `model` or `base_url`, or a `local` section with a `base_url`, is a
`ConfigError` when the engine is built, naming the problem.

Notes on the `local` provider:

- **No extra dependency.** The model ships with `chromadb`, which the engine
  already depends on for its built-in `chroma` index store.
- **One-time download.** The first indexing run downloads roughly 79 MB of ONNX
  model files into a local cache directory. Expect a pause of up to a few
  minutes; a log line is emitted before the download starts.
- **Embeddings only.** This removes the API key requirement from the embedding
  step. It does not make querying key-free: answering a question still needs a
  chat model (see [LLMs](llm.md)).

### `openai_compatible`

Any server with an OpenAI-style embeddings API: Azure OpenAI's v1 endpoint,
Ollama, vLLM, LiteLLM, LM Studio or a company gateway. There is no
Azure-specific provider; Azure uses this one.

```yaml
# embeddings.yaml: Ollama
provider: openai_compatible
base_url: http://ollama:11434/v1
model: nomic-embed-text
dimension: 768            # optional: skips the probe
```

```yaml
# embeddings.yaml: Azure OpenAI's v1 endpoint
provider: openai_compatible
base_url: https://my-resource.openai.azure.com/openai/v1/
api_key: "${env:AZURE_OPENAI_API_KEY}"
model: embed-prod          # the Azure deployment name
dimension: 1536
```

- **No key variable.** `OPENAI_API_KEY` is never read: that key belongs to
  OpenAI, not to your server. Write `api_key` (usually `${env:...}`) if the
  server needs one; without it a placeholder is sent, because the OpenAI client
  refuses to start without a key.
- **Text, not tokens.** The text is sent as is. (The `openai` provider sends
  OpenAI's token ids, which other servers' models cannot read.)

## Model and dimension

The engine needs the embedder's model and dimension to know which index it
reads and writes. A provider reports them as
`EmbeddingInfo(provider, model, dimension)`:

- `local` and `openai` with OpenAI's own models know the dimension.
- `openai_compatible` knows it only when you write `dimension:`.
- An `instance` reports its `model` attribute (or its class name) and the
  `dimension:` of the section, if any.

When the dimension is unknown, the engine embeds the text `"dimension probe"`
once, the first time it needs the dimension, and keeps the answer for the
engine's life. Building the engine never calls the model. Writing
`dimension:` skips that one call; it must be what the model returns.

## Writing a provider

A plugin implements `nl2sql.embeddings.EmbeddingsProvider`, interface
version 2, and registers it under the `nl2sql.embeddings` entry point group or
with `registry.add_embeddings` (see [the registry](../development/index.md#the-registry)):

```python
from nl2sql.embeddings import EmbeddingInfo


class MyProvider:
    interface_version = 2
    default_model = "my-model"
    requires = ("my_sdk",)
    install_hint = 'pip install "nl2sql-my-embeddings"'

    def create(self, cfg):
        """The LangChain embeddings object for ``cfg``. No model call; ValueError for a bad section."""
        ...

    def describe(self, cfg):
        """What ``create(cfg)`` embeds with. None as the dimension means "probe it"."""
        return EmbeddingInfo("mine", cfg.model or self.default_model, cfg.dimension)
```

## Switching providers

**Switching providers requires a re-index.** Different embedding models
produce different vectors, often of different dimensionality, so an existing
index cannot be read with another provider. Retrieving from an index whose vectors disagree with the
configured provider raises `EmbeddingDimensionMismatchError` naming both
providers. The check runs on the read path only, so the fix is to re-run
`nl2sql index --full`; alternatively, point [`storage.path`](storage.md) at a
separate folder per provider.
