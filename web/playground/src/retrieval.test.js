import { test } from "node:test";
import assert from "node:assert/strict";
import { asText, mmrLine, passedOver, picksInOrder, score, searchTitle, shortType, typeCounts } from "./retrieval.js";

const entry = (rank, label, similarity, pick_order = null, extra = {}) => ({
  rank, id: `schema.table:${label}:v1`, label, type: "schema.table", similarity,
  picked: pick_order !== null, pick_order, mmr_score: pick_order ? similarity : null, ...extra,
});

const search = {
  search: "tables",
  query: "Which genre sells the most tracks?",
  k: 2, fetch_k: 8, lambda_mult: 0.7,
  filter: { datasource_id: "chinook", types: ["schema.table", "schema.metric"] },
  pool: [entry(1, "Genre", 0.61, 1), entry(2, "Track", 0.58), entry(3, "InvoiceLine", 0.44, 2)],
  picks: ["schema.table:Genre:v1", "schema.table:InvoiceLine:v1"],
  dropped: ["schema.table:Track:v1"],
};

test("entry types read as plain words", () => {
  assert.equal(shortType("schema.relationship"), "join");
  assert.equal(shortType("schema.column"), "column");
  assert.equal(shortType("schema.custom"), "custom");
});

test("each engine search has a name", () => {
  assert.equal(searchTitle(search), "Table search");
  assert.equal(searchTitle({ search: "planning" }), "Columns and joins of the picked tables");
});

test("scores print to three places, withheld ones as a dash", () => {
  assert.equal(score(0.61234), "0.612");
  assert.equal(score(null), "-");
});

test("picks come back in the order MMR made them", () => {
  assert.deepEqual(picksInOrder(search).map((e) => e.label), ["Genre", "InvoiceLine"]);
});

test("an entry closer than a pick but dropped was passed over by MMR", () => {
  assert.deepEqual(passedOver(search).map((e) => e.label), ["Track"]);
  assert.deepEqual(passedOver({ pool: [entry(1, "A", 0.9, 1), entry(2, "B", 0.5)] }), []);
});

test("the MMR line states this search's numbers", () => {
  const line = mmrLine(search);
  assert.match(line, /Picked 2 of the 3 nearest entries \(k 2, pool up to 8\)/);
  assert.match(line, /0\.7 x similarity - 0\.3 x/);
  assert.match(mmrLine({ ...search, lambda_mult: 1 }), /simply the most similar/);
  assert.equal(mmrLine({ ...search, pool: [], picks: [] }), "Nothing in the index matched the filter.");
});

test("type counts summarise a mixed pool", () => {
  assert.deepEqual(typeCounts([{ type: "schema.table" }, { type: "schema.column" }, { type: "schema.table" }]),
    [{ type: "table", count: 2 }, { type: "column", count: 1 }]);
});

test("the text form is one fixed-width line per entry, for diffing", () => {
  const text = asText(search).split("\n");
  assert.equal(text[0], "query: Which genre sells the most tracks?");
  assert.equal(text[2], "filter: chinook table metric");
  assert.equal(text[5], "  1  pick 1    0.610   0.610  table       Genre");
  assert.equal(text[6], "  2  drop      0.580       -  table       Track");
});
