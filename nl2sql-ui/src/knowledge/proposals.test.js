// Proposals as the review list shows them, and the bodies of Accept and Reject. The fixture is the API's own
// `Proposal` (written by the A3 route test); the others are built from it so the shapes stay the real ones.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  acceptBody, acceptOutcome, changeLines, editableFields, evidenceLines, groupByKind, joinEnd, rejectBody, summaryOf,
} from "./proposals.js";

const term = JSON.parse(readFileSync(new URL("../api/fixtures/Proposal.json", import.meta.url), "utf8"));
const join = {
  ...term, id: "p-2", kind: "join", origin: "profiler", target: "Track.AlbumId->Album.AlbumId",
  change: { from: "Track.AlbumId", to: "Album.AlbumId", cardinality: "many-to-one", notes: "Found by value overlap." },
  evidence: { overlap: 0.98, probes: 3 },
};
const example = { ...term, id: "p-3", kind: "example", origin: "template", target: "How many customers?", change: { question: "How many customers?" } };
const column = { ...term, id: "p-4", kind: "column", target: "Invoice.Total", change: { description: "Order total", role: "money", synonyms: ["amount"] } };

test("proposals are grouped by kind in the order a reviewer reads them, and empty kinds are left out", () => {
  const groups = groupByKind([example, term, join, column]);
  assert.deepEqual(groups.map((g) => g.kind), ["term", "join", "column", "example"]);
  assert.deepEqual(groups.map((g) => g.label), ["Terms", "Joins", "Columns", "Examples"]);
  assert.deepEqual(groups[0].items.map((p) => p.id), ["p-1"]);
});

test("a kind this page does not know still shows, last, under its own name", () => {
  const odd = { ...term, id: "p-9", kind: "policy" };
  const groups = groupByKind([term, odd]);
  assert.deepEqual(groups.map((g) => g.kind), ["term", "policy"]);
  assert.equal(groups[1].label, "policy");
});

test("each kind has a one-line summary of what it would write", () => {
  assert.equal(summaryOf(term), "revenue: Sum of invoice totals");
  assert.equal(summaryOf(join), "Track.AlbumId to Album.AlbumId (many-to-one)");
  assert.equal(summaryOf(example), "How many customers?");
  assert.equal(summaryOf(column), "Invoice.Total: Order total");
});

test("only the text fields of a change can be edited, in the change's order", () => {
  assert.deepEqual(editableFields(term), [{ key: "definition", value: "Sum of invoice totals" }]);
  assert.deepEqual(editableFields(join), [{ key: "notes", value: "Found by value overlap." }]);
  assert.deepEqual(editableFields(column), [{ key: "description", value: "Order total" }]);
  assert.deepEqual(editableFields({ ...term, change: {} }), []);
});

test("evidence reads as key and value lines, never as rows", () => {
  assert.deepEqual(evidenceLines(join), ["overlap: 0.98", "probes: 3"]);
  assert.deepEqual(evidenceLines(term), []);
});

test("accepting several ids sends the ids, and only the edits that changed something", () => {
  const drafts = {
    "p-1": { definition: "Sum of invoice totals, in USD" },
    "p-2": { notes: "Found by value overlap." },
    "p-4": { description: "  Order total  " },
  };
  const byId = { "p-1": term, "p-2": join, "p-4": column };
  assert.deepEqual(acceptBody(["p-1", "p-2", "p-4"], drafts, byId), {
    ids: ["p-1", "p-2", "p-4"],
    edits: { "p-1": { definition: "Sum of invoice totals, in USD" } },
  });
});

test("accepting with no edits sends no edits at all", () => {
  assert.deepEqual(acceptBody(["p-1"], {}, { "p-1": term }), { ids: ["p-1"] });
  assert.deepEqual(acceptBody(["p-1"], { "p-1": { definition: "Sum of invoice totals" } }, { "p-1": term }), { ids: ["p-1"] });
});

test("an edit cleared to nothing is not sent: removing a field is a file edit, not a form one", () => {
  assert.deepEqual(acceptBody(["p-1"], { "p-1": { definition: "   " } }, { "p-1": term }), { ids: ["p-1"] });
});

test("rejecting sends the ids and a reason only when there is one", () => {
  assert.deepEqual(rejectBody(["p-1", "p-2"], "  Not USD.  "), { ids: ["p-1", "p-2"], reason: "Not USD." });
  assert.deepEqual(rejectBody(["p-1"], "   "), { ids: ["p-1"] });
});

test("an accept report says what was written, what was refused and why, and the warnings", () => {
  const out = acceptOutcome({
    accepted: [term, join], files_written: ["glossary.yaml"], warnings: ["An earlier source shadows revenue."],
    refused: { "p-4": "It is rejected, not proposed." },
  });
  assert.equal(out.summary, "Accepted 2 and wrote glossary.yaml.");
  assert.deepEqual(out.refused, [{ id: "p-4", why: "It is rejected, not proposed." }]);
  assert.deepEqual(out.warnings, ["An earlier source shadows revenue."]);
  assert.equal(acceptOutcome({ accepted: [], files_written: [], warnings: [], refused: { "p-4": "No such proposal." } }).summary,
    "Nothing was accepted.");
  assert.equal(acceptOutcome({ accepted: [term], files_written: [], warnings: [], refused: {} }).summary, "Accepted 1.");
});

test("every entry of a change is shown, so a role-only proposal says what it would write", () => {
  const roleOnly = { ...term, id: "p-5", kind: "column", target: "Invoice.Total", change: { role: "money", unit: "USD", values: { a: 1 } } };
  assert.deepEqual(changeLines(roleOnly), ["role: money", "unit: USD", 'values: {"a":1}']);
  assert.deepEqual(changeLines({ ...term, change: { synonyms: ["amount", "total"] } }), ["synonyms: amount, total"]);
  assert.deepEqual(changeLines({ ...term, change: {} }), []);
});

test("a join's composite ends are listed apart", () => {
  assert.equal(joinEnd(["a", "b"]), "a, b");
  assert.equal(joinEnd("a"), "a");
});
