# Web app

The web app is the browser front end of NL2SQL. It lives in `nl2sql-ui/` at the
repository root, is built into the API package, and talks to nothing but the
[REST API](../api/index.md) through `nl2sql-ui/src/api/http.js`. Anything the
page can do, an SDK or REST caller can do the same way.

Pages are reached by hash route: `#/chat`, `#/approvals`, `#/settings`,
`#/retrieval`, `#/pipeline`. A route nobody serves opens Home.

## Chat

`#/chat` (and `#/chat/<conversation id>`) is a [conversation](../conversations/index.md)
with the model-led chat. The model decides which steps to take; the page shows
what it did.

**The list and the transcript.** The left column lists your conversations
(`GET /conversations`), with **New conversation** and a delete button on each.
The transcript shows one block per stored turn. A new conversation starts on the
first question, so the address bar names it from then on and a reload comes back
to the same place. A new conversation also offers the datasource's starter
questions and, with more than one datasource, a choice of which one to ask.

**A turn streams.** A question is sent to `POST /conversations/{id}/turns/stream`.
While it runs, the page shows the run spine (the pipeline stations as they start
and finish, with their timings) and the answer text as it arrives. The last
event carries the stored turn, which replaces the live view, so what stays on
the page is exactly what the server saved. **Stop** cancels the stream. A second
turn sent while one is running is refused by the server with 409, and the page
says so.

**What an answered turn shows.** The answer text and the rows come first. Below
them, in this order:

- warnings about the result (truncated rows, withheld columns, and so on);
- **Why this answer**, a fold with the sections of the explanation (how the
  question was read, which tables and curated answers were used, what the
  planner chose; the planner's note is marked as the model's own words);
- **SQL** and **Plan**, the typed plan and the query it produced;
- **Trace and cost**, the run's stations, timings and token use;
- a rating (**Right** or **Wrong**, with a note), and a corrected SQL that
  **Send for review** files (`POST /feedback`, `POST /feedback/correct`).

**Clarification.** When the model needs a decision, the turn shows its question
and an option for each reading. **Use this reading** sends `{choice: option id}`
as the next turn. **Remember this meaning** also files the pick, with an
optional term, as a proposal for review (`POST /knowledge/choices`).

**Approvals.** When a plan needs approval, the turn lists what is waiting. Once
every request is granted, **Run it now** calls `POST /conversations/{id}/retry`
with the approval ids. **Check again** reads the requests' state; a denied request says so and the turn stays stopped.

**More with this answer.** Buttons under a turn run the conversation's tools
(`GET /chat-tools`, `POST /conversations/{id}/tools/{name}`): **Chart**,
**Diagram**, **Report**, and **Pin to dashboard** when the conversation belongs
to a dashboard. A tool the model offers in its reply gets a button too. Tool
arguments use 1-based `turn` and `step` numbers.

## Approvals

`#/approvals` lists the requests waiting for a decision (`GET /approvals?status=pending`),
each with the question, who asked, the plan, the SQL it would run and why it
stopped. **Grant** and **Deny** call the matching routes. The nav item shows how
many are waiting; a caller who may not see approvals sees no count.

## Source map

| Path | What it holds |
| --- | --- |
| `src/api/chat.js` | Every conversation, approval, feedback and trace call |
| `src/chat/spine.js` | Folds stream events into the live run spine |
| `src/chat/turns.js` | Reads a stored turn: answer, steps, pending questions, tool buttons |
| `src/pages/Chat.jsx` | The page: list, transcript, composer, streaming |
| `src/pages/Approvals.jsx` | The approvals queue |

Where the code and `docs/architecture/wave_d.md` differ, the code is right.
