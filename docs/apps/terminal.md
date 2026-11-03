# Terminal app

`nl2sql chat` is the chat of the [web app](web.md) in a terminal: ask a question, follow up, see
why the answer is what it is, chart it, approve a query that needs it. It uses the keyboard only and is
part of the `nl2sql-cli` package (Textual is a normal dependency of it), so `pip install nl2sql-cli` is all it takes.

```bash
nl2sql chat                            # the project in ./nl2sql.yaml (or --config PATH)
nl2sql chat --demo                     # the demo project in ./nl2sql-demo, answered from recordings when there is no key
nl2sql chat --as analyst --user ana    # ask as a role and a user
nl2sql chat --datasource chinook       # one datasource only
```

| Option | Meaning |
| --- | --- |
| `--demo`, `--dir` | Chat on the demo project in `--dir` (default `nl2sql-demo`); it is written and indexed on first use, as `nl2sql demo` does |
| `--as ROLE` | The caller's role. The default is the project's `policies.default_role`; with none, the caller has no role |
| `--user NAME` | The caller's user id. The default is the operating-system user |
| `--datasource ID` | Start conversations on this datasource and offer only its starter questions |

The app takes its keys from the environment and the project's `.env`, like every command; it never asks for
one. Every call it makes names the caller, so the policy of the role holds here as it does in the REST API.

## The screen

Conversations are on the left, the transcript in the middle and the question box at the bottom. A side
panel on the right is hidden until you ask for it.

| Key | Does |
| --- | --- |
| `Enter` | Send the question (or run the `/command`) |
| `Tab` | In the question box, fill the next starter question of the datasource; elsewhere, move the focus |
| `F2` | Show or hide *why this answer*: the plan in plain words |
| `F3` | Show or hide the SQL that ran |
| `PageDown` | Read the next page of rows into the table |
| `Ctrl+O` | Move to the list of conversations; `Enter` opens one |
| `Ctrl+N` | Start a new conversation |
| `Esc` | Back to the question box |
| `Ctrl+Q` | Quit |

A turn streams: a status line shows the node or tool that is running, then the answer, any warnings, and the
rows as a table (the first page; `PageDown` reads on). When the model took several steps, each is listed
before the reply.

## Commands

Type these in the question box.

| Command | Does |
| --- | --- |
| `/chart [line, bar, big_number or table]` | Chart the last result. A `line` is one sparkline per series with its first and last value, a `bar` is one row of block characters per category, a `big_number` is the figure |
| `/diagram` | Show the Mermaid text of the schema diagram (a terminal cannot draw it) |
| `/report`, `/save FILE` | Show the conversation as a report; write it to a file |
| `/approvals` | List the approval requests waiting for your role |
| `/remember` | After picking a reading of an ambiguous phrase: save the pick as a proposal for review |
| `/new`, `/help` | New conversation; the keys and commands |

Any other `/name` runs the chat tool of that name.

## Clarifying and approving

- **A phrase that can be read two ways** opens a list of the readings. Pick one with the arrow keys and `Enter`;
  the question runs again with your choice. `/remember` then saves the choice as a proposal; it reaches the
  glossary only when a reviewer accepts it.
- **A query that needs approval** shows the reason and the SQL. If your role is one of the approvers, **Approve
  and run** grants the request and retries the turn; otherwise the app says which roles can approve. An approver
  can list what is waiting with `/approvals`.

## Where it lives

`nl2sql-cli/src/nl2sql_cli/chat/` holds the app (`app.py`), its widgets and the drawing of a chart as text
(`charts.py`); the command is `nl2sql_cli/commands/chat.py`. Only that folder imports `textual`, and it imports
only the top-level `nl2sql`, like the rest of the CLI. `import nl2sql` and the other commands load no Textual.
