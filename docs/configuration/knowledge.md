# Knowledge

The glossary is what people know about their data and the database does not
say: table and column descriptions, synonyms, column roles, units, grains and
code values, joins the database does not declare, business terms, prose rules
and example questions. It is written in `glossary.yaml` and read by a
*knowledge source*; the `knowledge` section lists the sources.

!!! note "Release 0.8"
    The engine loads, merges, shows and exports knowledge from release 0.8.
    Knowledge reaches the planner's prompts with Wave B's K2 and the index with
    K3; until then it is shown and checked, not used to plan. See
    [Wave B](../architecture/wave_b.md), section 3.

## The section

```yaml
# nl2sql.yaml
knowledge: { file: knowledge.yaml }

# knowledge.yaml: a list, in precedence order
- type: yaml
  path: glossary.yaml             # relative to nl2sql.yaml
- type: corp_catalog              # a plugin; its config_model checks the rest
  url: "${env:CATALOG_URL}"
```

```python
from nl2sql import Config, KnowledgeSourceConfig

Config(..., knowledge=[KnowledgeSourceConfig(type="yaml", path="glossary.yaml"), MySource()])
```

| Rule | Detail |
| --- | --- |
| **No section** | No knowledge: the pipeline is 0.7's. `nl2sql init` writes `knowledge.yaml` naming one `yaml` source and a `glossary.yaml` with one empty block per datasource; `nl2sql init --demo` writes the demo glossary. |
| **Loaded at build and on reload only** | Each source's `load()` runs when the engine is built and on `engine.knowledge.reload()`, never during a run. |
| **Fails closed at build** | A source that cannot be built or loaded, a missing `glossary.yaml`, or a text longer than `settings.knowledge.item_max_chars` is a `ConfigError` naming the source and the item. |
| **Fails open on reload** | A reload that fails keeps the previous knowledge live; the source's `error` says why. |
| **A config built in code** | A relative `path` is read from the current folder. |
| **Objects skip the registry** | A source object in code (one with `load`) is used as given, named by its `type` attribute or its class. |

## `glossary.yaml`

```yaml
version: 1
datasources:
  chinook:
    tables:
      Invoice:
        description: One row per customer invoice. Total includes tax.
      InvoiceLine:
        description: One row per track sold on an invoice.
        notes: Refunds are not recorded anywhere.     # for people; never sent to a model
    columns:
      Invoice.Total:
        description: Invoice total in US dollars, tax included.
        synonyms: [invoice amount, bill total]
        role: money
        unit: USD
      Invoice.InvoiceDate:
        grain: day                                   # role: date is derived from the type
      Track.MediaTypeId:
        role: enum
        values: {"1": MPEG audio file, "2": Protected AAC audio file}
    joins:
      - from: Track.MediaTypeId
        to: MediaType.MediaTypeId
        cardinality: many-to-one
    terms:
      - term: revenue
        synonyms: [sales, turnover]
        definition: Sum of InvoiceLine.UnitPrice times InvoiceLine.Quantity. Excludes tax.
        columns: [InvoiceLine.UnitPrice, InvoiceLine.Quantity]
    rules:
      - id: no_test_customers
        text: Customers whose email ends in @example.com are test accounts; leave them out of counts and sums.
        tables: [Customer]
    examples:
      - Which genres sell best in Germany?
```

| Item | Fields |
| --- | --- |
| A table | `description`, `notes` |
| A column (`table.column`) | `description`, `notes`, `synonyms`, `role` (`identifier`, `enum`, `money`, `quantity`, `date`, `text`), `unit`, `grain` (`second` to `year`), `values` (a code, written as a string, and its meaning; on `role: enum` only) |
| A join | `from`, `to` (`table.column`, or a list of them for a composite key), `cardinality` (default `unknown`), `notes` |
| A term | `term`, `synonyms`, `definition`, `columns`, `tables` |
| A rule | `id` (a slug, unique in its datasource), `text`, `tables` (empty: every sub-query on the datasource) |
| An example | a question |

Every model is strict: an unknown key is an error. Every text is at most
`settings.knowledge.item_max_chars` (500) characters; a longer one is a load
error naming the item, so nothing is silently cut. `notes` is for people: it
is shown by `knowledge show` and never sent to a model.

### Names

A table is named as `preferred_tables` names it: the bare name or
`schema.table`, case ignored. A column is `table.column` or
`schema.table.column`. Names are resolved against the datasource's indexed
schema when the knowledge is used, not when it is loaded:

| Case | What happens |
| --- | --- |
| Resolves to one table or column | Used |
| Resolves to nothing (a typo, a dropped column) | Not used; a problem in `knowledge show`, never an error in a run |
| A bare name matching tables in two schemas | Not used; the problem says to write `schema.table` |
| A join whose two sides' types cannot be compared (a date against a number or a boolean) | Not used; the problem names both types |

A schema drift check (`engine.index.check`) lists the glossary items that
named a dropped table or column in its impact's `glossary_items`.

## Precedence

Several sources are merged in config order. A single value (a description,
a role, a unit) is the first source's that sets it; lists (synonyms, terms,
rules, joins, examples) are unioned, a duplicate (the same term name, rule id
or join, case ignored) kept from the first. Each item records the source it
came from.

A table or column then has at most one description, and each column one
role, chosen in this order:

| Origin | Where it is kept | Who wrote it |
| --- | --- | --- |
| `knowledge` | A knowledge source, in config order | A person |
| `database` | The table or column comment the adapter read | The data owner, in the database |
| `derived` | Nowhere: computed from the schema. A role only: `identifier` for a primary or foreign key column, `date` for a temporal type | The schema itself |

Each datasource's merged knowledge has a digest (the SHA-256 of its
canonical JSON), shown by `knowledge show` and published by
`knowledge.loaded`.

## Who sees what

Every method takes the caller as a required keyword (`user_context`), with
no admin default. A caller sees only what its scope reaches: an item that
names a table it cannot read (a table, a column of one, either side of a
join, a term's or a rule's tables or columns), or whose text mentions such a
table by name (a whole word, case ignored), is left out. Knowledge therefore
never names a hidden table. A paraphrase ("the pay table") cannot be caught
this way; the validator still refuses any plan on the table.

Checking names against the scope needs the datasource's indexed schema, so a
datasource that is not indexed yet shows nothing but that problem.

Knowledge is data, never instructions: it is validated as data when it
loads, `export` writes it with a safe YAML dumper, and it reaches a model
only as JSON values (Wave B's K2).

## The facade and the CLI

| Method | What it does |
| --- | --- |
| `engine.knowledge.sources(*, user_context)` | Each source (`KnowledgeSourceInfo`): its `type`, item counts per datasource the caller reaches (counting what the caller may see), `sha256`, `loaded_at` and the last reload's `error` |
| `engine.knowledge.show(datasource_id=None, *, user_context)` | The merged knowledge as the engine uses it (`DatasourceKnowledgeView`): `knowledge` with derived roles and database comments filled in, `origins` and `sources` per item, `digest` and `problems` |
| `engine.knowledge.export(path=None, *, datasource_ids=None, user_context)` | The knowledge-source items the caller may see as one canonical `glossary.yaml`, returned and, with `path`, written there. It refuses to write over a config file or a knowledge source's own file. Derived roles and database comments are not exported. |
| `engine.knowledge.reload(*, user_context)` | Loads every source again and swaps atomically; needs an admin role (`policies.admin_roles`; any caller under `allow_all`). Runs in flight keep the knowledge they started with. |

```python
from nl2sql import NL2SQL, UserContext

engine = NL2SQL.from_config("nl2sql.yaml")
analyst = UserContext(roles=["analyst"])
for view in engine.knowledge.show(user_context=analyst):
    print(view.datasource_id, [t.term for t in view.knowledge.terms], view.problems)
```

| Command | Calls |
| --- | --- |
| `nl2sql knowledge show [--datasource ID] --as ROLE` | `show`: a readable listing, each item with its origin; problems last, exit 1 when there are any |
| `nl2sql knowledge export [--datasource ID] [--out FILE] --as ROLE` | `export`, to stdout or `--out` |
| `nl2sql knowledge sources --as ROLE` | `sources` |

`--as` (repeatable) and `--user` build the caller. Without `--as` the role is
`policies.default_role`; with neither the command refuses. Under `allow_all`
no role is needed.

## The canonical form

`glossary.yaml` belongs to people, and the engine rewrites it only when a
reviewer accepts a proposal (Wave B's review queue). It then writes the
canonical form: a fixed header comment, the keys in the models' order,
tables, columns and code values sorted by name, lists in their own order.
Comments are not kept, so write remarks in a `notes:` field. The write
refuses when the file changed since the engine loaded it, writes through a
symlink (the link is kept), and replaces the file atomically. `export`
writes the same canonical form, and the demo glossary is kept in it.

## The `KnowledgeSource` interface

A plugin reads knowledge from elsewhere (a wiki, a data catalog). It depends
on the SDK only (`nl2sql_sdk.knowledge`) and is installed under the
`nl2sql.knowledge_sources` entry point group:

```python
from pathlib import Path
from typing import ClassVar
from pydantic import BaseModel, ConfigDict
from nl2sql_sdk.knowledge import Glossary


class CatalogConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str


class CatalogSource:
    interface_version: ClassVar[int] = 1
    config_model: ClassVar[type[BaseModel]] = CatalogConfig

    @classmethod
    def from_config(cls, cfg: CatalogConfig, *, folder: Path) -> "CatalogSource":
        return cls(cfg.url)

    def __init__(self, url: str):
        self.url = url

    def load(self) -> Glossary:
        return Glossary.model_validate(fetch_glossary(self.url))
```

A source only reads. The engine checks every text a source returns against
`settings.knowledge.item_max_chars`, and a source that raises is a load
error. See [The SDK](../adapters/sdk.md).
