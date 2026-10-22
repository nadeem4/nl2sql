# The Review Queue

Every change the engine suggests to a person's knowledge or curated answers is
a *proposal* in one queue, kept in the state store (`storage.state`). A
thumbs-up on a generated answer files a proposed verified query, and so does a
checked [correction](../observability/feedback.md#correcting-an-answer-nl2sql-feedback-correct)
(`engine.feedback.correct`, origin `correction`); later 0.8 work files the
profiler's join candidates and column roles, drafted descriptions and saved
clarifications the same way. Nothing in
the queue reaches a prompt, the index or a file until a person with an admin
role accepts it.

```bash
nl2sql review list --as admin                     # what is waiting
nl2sql review show 3f9a0c1e5b7d2a44 --as admin    # one, beside the current value
nl2sql review accept 3f9a0c1e5b7d2a44 --as admin  # write it
nl2sql review reject 9b1e44c07a2d1f30 --as admin --reason "Not USD."
```

## Proposals

A proposal is the SDK's `Proposal` (`from nl2sql import Proposal`), so a
third-party state store can keep it:

| Field | What it holds |
| --- | --- |
| `id` | 16 hex characters: SHA-256 of `kind`, `datasource_id`, `target` and the canonical JSON of `change` |
| `kind` | `join`, `column`, `table` or `term` (the glossary), or `verified_query` (`curated.yaml`) |
| `target` | What it changes: `Invoice.Total`, `Track.MediaTypeId->MediaType.MediaTypeId`, a term's name, a question |
| `change` | The fields it would set, in `glossary.yaml`'s or `curated.yaml`'s shape |
| `tables` | Every `datasource.schema.table` its target and change name, for the scope filter. Filing completes it: a table, a column's table, both sides of a join, a term's tables and columns' tables, and the tables a verified query's SQL reads are always listed |
| `origin` | `thumbs_up`, `profiler`, `draft`, `correction` or `clarification` |
| `evidence` | Counts and checks (a thumbs-up's trace id and role; a correction's trace id, original SQL, `ran`, `row_count` and `columns`); never rows |
| `status` | `proposed`, `accepted`, `rejected` or `superseded` |
| `created_at`, `created_by`, `decided_at`, `decided_by`, `reason`, `wrote` | Who and when; a rejection's reason; the file an accept wrote |

| Rule | Detail |
| --- | --- |
| **One change, one row** | The id hashes the change, so filing the same change again updates the open row's evidence and adds any tables the new filing names. A rejected change keeps its row and is never proposed again; a different change for the same target is a new row. |
| **Supersede** | A new change for a kind, datasource and target that already has an open row marks that row `superseded`: the queue holds one open item per target. Accepted and rejected rows are never superseded. |
| **Retention** | Decided rows (accepted, rejected, superseded) are kept for `settings.review.max_age_days` (365) and deleted by `engine.storage.purge()` (`nl2sql storage purge`, and `nl2sql-api` once a day). Open rows are never purged. |

## Who sees and who decides

Reading the queue takes the caller, with no default (as every Wave B read
does): a caller sees a proposal only when their scope reaches its datasource
and every table in its `tables`, and `get` of one they cannot see is "not
found", the same answer as an id that does not exist.

Accepting and rejecting take `by`, a `UserContext` holding one of
`policies.admin_roles` (`["admin"]` by default); anyone else gets
`ReviewError` with code `NOT_AUTHORIZED`. Under `policies.allow_all` there are
no roles, and every caller may decide. Like an approval, `by` is trusted as far
as the caller's `UserContext` is.

## The facade

```python
from nl2sql import UserContext

admin = UserContext(user_id="ada", roles=["admin"])
waiting = engine.proposals.list(user_context=admin, kind="verified_query")
report = engine.proposals.accept([p.id for p in waiting], by=admin,
                                 edits={waiting[0].id: {"description": "Revenue by month."}})
report.accepted, report.files_written, report.warnings, report.refused
```

| Method | What it does |
| --- | --- |
| `list(*, user_context, kind=None, status="proposed", datasource_id=None, limit=None)` | The proposals the caller may see, newest first; `status=None` lists every status |
| `get(id, *, user_context)` | One proposal; `ReviewError(NOT_FOUND)` when it is missing or hidden |
| `current(id, *, user_context)` | What the proposal would replace: the glossary item as the caller sees it, or the curated query with the same id. None when there is none, or when the caller's scope does not reach every table that query reads |
| `accept(ids, *, by, edits=None)` | Accepts one id or a list; `edits` maps an id to a dict merged over its `change`. Returns an `AcceptReport` |
| `reject(ids, *, by, reason=None)` | Rejects one id or a list; every id must be proposed, or nothing is rejected (`NOT_FOUND`) |
| `export(path, *, user_context, kind=None, datasource_id=None)` | Writes the open proposals the caller may see as a [review file](#the-review-file); returns how many. Never over `nl2sql.yaml`, a section file or a knowledge source's file |
| `apply(path, *, by)` | Reads a review file: accepts each `accept` (the file's `change` replaces the stored one, so a key can be removed) and rejects each `reject` (with its `reason`) |

`AcceptReport` has `accepted` and (from `apply`) `rejected`, the proposals now
decided; `files_written`, each file written once, relative to the folder of
`nl2sql.yaml`; `warnings`; and `refused`, an id to why it was left as it was:
unknown, no longer proposed, or a change that failed its checks.

## Accept: the only writer

Accept is the only code that writes a file a person owns, and it writes two:

| Kind | Writes | Then |
| --- | --- | --- |
| `join`, `column`, `table`, `term` | The first `yaml` [knowledge source](knowledge.md)'s `glossary.yaml`, each change merged in. A `term` whose name exists replaces that term. | The knowledge is reloaded: the items are in the next run's prompts. |
| `verified_query` | The `curated` section's own file (`curated: {file: curated.yaml}`), the query added under its datasource | The curated checks run first, on the edited section. Then the engine's curated answers are swapped and the datasource's curated index entries refreshed (as `nl2sql index --curated-only` does), so the next question can match it. |

| Rule | Detail |
| --- | --- |
| **One write per file** | The accepted items are grouped by file; each file is locked, checked, applied, written and reloaded once. Accepting a thousand column roles is one write. |
| **Locked** | Each file's write holds the state store's lock `accept:<real path>`, so two accepts in two threads or processes cannot both pass the check and lose a write; one waits up to 10 seconds for the other. Each file's items are recorded as accepted under the lock, right after its write, and `reject` takes the same locks, so an id is never accepted twice, nor rejected while an accept is writing it. |
| **Changed underneath** | Each file's SHA-256 is recorded when the engine loads it; for `curated.yaml`, only when the file still holds the section the config loaded (otherwise every accept refuses). When the file on disk no longer matches, accept refuses with `FILE_CHANGED`: reload the knowledge (`engine.knowledge.reload`) for the glossary, or load the config again and build a new engine for `curated.yaml`. Every file is checked before the first write, so a call refused by these checks writes nothing. If a later file's write fails after an earlier one succeeded, the earlier file's items stay accepted and the later file's stay proposed. A `${...}` reference in `curated.yaml` reads as a change: keep the file literal. |
| **Where** | Without a `yaml` knowledge source, a glossary accept is `NOT_WRITABLE`. A `curated` section written inline in `nl2sql.yaml`, or in a config built in code, is never written (`NOT_WRITABLE`: move it to its own file, or export and edit by hand); a missing section is refused with the fix "add `curated: {file: curated.yaml}`". |
| **Per item** | An item whose change fails its checks (the glossary's validation, the curated checks, a verified query id that is taken) stays proposed and is named in `refused`; the rest are written. |
| **Canonical rewrite** | Both files are rewritten in canonical form: the models' key order, mappings sorted by name, lists in their own order, SQL and multi-line text as literal blocks, under a fixed header. **Comments are not kept**: write remarks in `notes:` (glossary) or `description:` (curated). |
| **Atomic, through links** | The real path is resolved first, so a symlinked file is written through and the link kept; the new text goes to a temporary file beside it, renamed over it. The proposals are marked accepted, with `wrote`, after the rename. |
| **Shadowed** | A knowledge source listed before the glossary that sets the same item wins the merge; accept still writes, and says so in `warnings`. |

`proposal.created` is published when a proposal enters the queue and
`proposal.decided` (`decision`, `by`, `wrote`) for each accept and reject; the
audit observer keeps `proposal.decided`. See
[Events](../observability/events.md).

## The review file

The queue's export and import format, for deciding in a pull request:

```yaml
# review.yaml: written by `nl2sql review export`, read by `nl2sql review apply`
version: 1
proposals:
- id: 3f9a0c1e5b7d2a44
  decision: accept            # accept | reject | (empty: leave proposed)
  reason: null
  kind: join
  datasource_id: chinook
  target: Track.MediaTypeId->MediaType.MediaTypeId
  change: {from: Track.MediaTypeId, to: MediaType.MediaTypeId, cardinality: many-to-one}
  evidence: {name_match: exact, types: [number, number], sampled: 347, matched: 347, overlap: 1.0}
  origin: profiler
```

A reviewer sets `decision` and may edit `change` and `reason`. `apply` reads
back only `id`, `decision`, `reason` and `change`; it accepts each `accept`
with the file's `change` in place of the stored one, rejects each `reject`, leaves an empty decision
proposed, and names in `refused` each id that is unknown or no longer
proposed.

## The CLI

Every command reads as the caller `--as ROLE` (repeatable) and `--user ID`
build; without `--as`, the role is `policies.default_role`.

| Command | Calls |
| --- | --- |
| `nl2sql review list [--kind K] [--datasource ID] [--status S]` | `list`; `--status all` lists every status |
| `nl2sql review show ID` | `get`, with `current` beside the proposed change |
| `nl2sql review accept ID... [--all --kind K --datasource ID]` | `accept`; `--all` accepts every open proposal the caller sees. Exits 1 when an item is refused |
| `nl2sql review reject ID... [--reason TEXT]` | `reject` |
| `nl2sql review export --out FILE [--kind K] [--datasource ID]` | `export` |
| `nl2sql review apply FILE` | `apply`; exits 1 when an item is refused |

A refusal (`ReviewError`) prints its message and fix and exits 1.

## Source references

- The queue (identity, supersede, visibility, the admin check): `packages/nl2sql/src/nl2sql/review/queue.py`
- Accept and reject: `packages/nl2sql/src/nl2sql/review/accept.py`
- The curated file's canonical writer: `packages/nl2sql/src/nl2sql/review/curated_file.py`
- The review file: `packages/nl2sql/src/nl2sql/review/review_file.py`
- The facade: `packages/nl2sql/src/nl2sql/api/proposals_api.py`
- Thumbs-up proposals: `packages/nl2sql/src/nl2sql/api/feedback_api.py`, `packages/nl2sql/src/nl2sql/feedback/proposals.py`
- Corrections: `packages/nl2sql/src/nl2sql/feedback/corrections.py`, and `nl2sql feedback correct` in `packages/nl2sql/src/nl2sql/cli/commands/feedback.py`
- The command: `packages/nl2sql/src/nl2sql/cli/commands/review.py`
- The design: [Wave B, section 5](../architecture/wave_b.md#5-the-review-queue)
