# Knowledge

The glossary is what people know about their data and the database does not
say: table and column descriptions, synonyms, column roles, units, grains and
code values, joins the database does not declare, business terms, prose rules
and example questions. It is written in `glossary.yaml` and read by a
*knowledge source*; the `knowledge` section lists the sources.

!!! note "Release 0.8"
    The engine loads, merges, shows and exports knowledge from release 0.8,
    and the planner and refiner read it (Wave B's K2): descriptions, roles,
    units, grains, code values and glossary joins in the schema block, matched
    terms and rules in `[BUSINESS_CONTEXT]`. It reaches the index too (K3): the
    chosen descriptions, the terms, the joins and the examples are index entries,
    and `nl2sql index --refresh` brings them up to date after an edit without
    reading the schema. See
    [Wave B](../architecture/wave_b.md), sections 3 and 4, and
    [the schema retriever](../architecture/nodes/schema_retriever_node.md#knowledge).

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
error naming the item, so nothing is silently cut. Every name (a table, a
column, a code, a rule id, a term's tables and columns) is at most 255
characters, and the `yaml` source reads a glossary file of at most 5 MB.
`notes` is for people: it is shown by `knowledge show` and never sent to a
model.

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
table by name (a whole word, case ignored), is left out. The texts checked
are every free text and every name an item carries, a rule's id and an enum
column's codes included. The names checked are the datasource's indexed
tables the caller cannot read, plus every table one of its roles denies by
name, so a table kept out of the index is covered too. Knowledge therefore
never names a hidden table by its exact name.

What the check cannot catch: a paraphrase ("the pay table"), a plural or
singular ("salary" for `salaries`), and a name inside a longer identifier
(`salaries_history`), as well as a table hidden only by a wildcard pattern
and not indexed. The validator still refuses any plan on the table.

Checking names against the scope needs the datasource's indexed schema, so a
datasource that is not indexed yet shows nothing but that problem.

Knowledge is data, never instructions: it is validated as data when it
loads, `export` writes it with a safe YAML dumper, and it reaches a model
only as JSON values, filtered by the caller's scope (see
[Knowledge Is Data](../architecture/invariants.md#knowledge-is-data-the-validator-and-scope-decide)).
A glossary join between two tables the caller may read is a relationship the
validator accepts, so a database without declared keys can be joined. A
changed glossary changes the datasource's knowledge digest, which keys the
plan cache: the next question plans again.

## The facade and the CLI

| Method | What it does |
| --- | --- |
| `engine.knowledge.sources(*, user_context)` | Each source (`KnowledgeSourceInfo`): its `type`, item counts per datasource the caller reaches (counting what the caller may see), `sha256`, `loaded_at` and the last reload's `error`. The error quotes the file (its path, its lines, the tables it names), so only a caller who may reload (an admin role, or any caller under `allow_all`) sees it; anyone else gets a fixed text saying the reload failed and an admin can see why |
| `engine.knowledge.show(datasource_id=None, *, user_context)` | The merged knowledge as the engine uses it (`DatasourceKnowledgeView`): `knowledge` with derived roles and database comments filled in, `origins` and `sources` per item, `digest` and `problems` |
| `engine.knowledge.export(path=None, *, datasource_ids=None, user_context)` | The knowledge-source items the caller may see as one canonical `glossary.yaml`, returned and, with `path`, written there. It refuses to write over a config file or a knowledge source's own file. Derived roles and database comments are not exported. |
| `engine.knowledge.profile(datasource_ids=None, *, user_context, draft=False, max_cost_usd=None, on_datasource=None, cancellation_token=None)` | Probes the indexed schema for joins the database does not declare and for column roles, and files them in the [review queue](review.md); one `ProfileReport` per datasource the caller reaches. See [Schema Profiler](../schema/profiler.md). `draft=True` also drafts table and column descriptions with the `profiler` model, as proposals, under `max_cost_usd`. `on_datasource` is called with each datasource's `ProfileReport` as it finishes; cancelling `cancellation_token` stops before the next datasource and returns the reports so far. |
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
| `nl2sql profile [--datasource ID] --as ROLE` | `profile`, then the open profiler proposals; `nl2sql index --profile --as ROLE` indexes first |

`--as` (repeatable) and `--user` build the caller. Without `--as` the role is
`policies.default_role`; with neither the command refuses. Under `allow_all`
no role is needed.

## The canonical form

`glossary.yaml` belongs to people, and the engine rewrites it only when a
reviewer accepts a proposal ([the review queue](review.md)). It then writes the
canonical form: a fixed header comment, the keys in the models' order,
tables, columns and code values sorted by name, lists in their own order.
Comments are not kept, so write remarks in a `notes:` field. The write
refuses when the file changed since the engine loaded it, writes through a
symlink (the link is kept), and replaces the file atomically: the new text
goes to a temporary file, flushed to disk and given the file's mode, which is
renamed over it. A file another program holds open (on Windows) is refused
with a review error saying to close it. `export`
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
