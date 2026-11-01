# Knowledge API

What the engine knows about each datasource, and the proposals that change it. Thin over `engine.knowledge` and
`engine.proposals`; reads pass the caller, so a datasource the caller's scope hides is a `404`. Every `/proposals`
route is admin only (a proposal can name a table the caller's scope hides, and accepting one writes a file); see
[Admin routes](index.md#admin-routes). Source: `packages/api/src/nl2sql_api/routes/knowledge.py`.

## Knowledge

| Route | Does |
| --- | --- |
| `GET /api/v1/knowledge` | The merged knowledge per datasource the caller may see (`datasource_id` narrows it): each item with where it came from, and the `problems` (names that do not resolve, joins whose types cannot be compared). |
| `GET /api/v1/knowledge/sources` | Each knowledge source (`yaml`, ...): counts per datasource, checksum, load time, last reload error. |
| `POST /api/v1/knowledge/reload` | Admin. Load every source again and swap atomically; a failing source keeps its previous knowledge and says why in `error`. |
| `POST /api/v1/knowledge/choices` | Body `{"choice": <ClarificationChoice>, "term": null}`. Files the caller's clarification pick as a `term` proposal and returns it; nothing changes until an admin accepts. |

## Proposals

| Route | Does |
| --- | --- |
| `GET /api/v1/proposals` | Newest first. Filters `kind`, `status` (default `proposed`), `datasource_id`, `limit` (1 to 500). |
| `GET /api/v1/proposals/{proposal_id}` | One proposal. |
| `POST /api/v1/proposals/accept` | Body `{"ids": [...], "edits": {"<id>": {...fields merged over the change}}}`. Writes each file once; returns `AcceptReport` (`accepted`, `files_written`, `warnings`, `refused` by id). |
| `POST /api/v1/proposals/reject` | Body `{"ids": [...], "reason": "..."}`. A rejected change is never proposed again. |
| `POST /api/v1/proposals/examples` | Body `{"datasource_id": "...", "questions": [...]}`. Files each question as an `example` proposal. |

Unknown keys in a body are a `422`. Errors are the [error envelope](errors.md): an unknown proposal is `404 NOT_FOUND`,
a file that changed on disk is `409 FILE_CHANGED`, a config with no writable knowledge file is `403 NOT_WRITABLE`.

## Start a profile

`POST /api/v1/jobs/profile` (admin, `202`). Body: `{"datasource_ids": null, "draft": false, "max_cost_usd": null}`
(`max_cost_usd` above 0; unknown keys a `422`). It runs `engine.knowledge.profile(...)` as a [job](jobs.md), one step
per datasource, and files what it finds as proposals. It always uses the server's key: a key typed in a browser never
pays for drafts. The job's `result` (owner only) is a list of profile reports.
