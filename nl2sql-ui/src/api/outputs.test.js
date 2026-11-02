import { test } from "node:test";
import assert from "node:assert/strict";
import { getChart, getResultPage, downloadCsv, downloadVegaLite } from "./outputs.js";

// A fetch that records the calls and answers each with a JSON body (or text for the downloads).
function fakeFetch(body) {
  const calls = [];
  globalThis.sessionStorage = { getItem: () => null, setItem() {}, removeItem() {} };
  globalThis.fetch = async (url, init) => {
    calls.push(String(url));
    return new Response(typeof body === "string" ? body : JSON.stringify(body), { status: 200 });
  };
  return calls;
}

test("getChart asks for the spec of a result, with a kind only when one is forced", async () => {
  const calls = fakeFetch({ kind: "bar" });
  assert.deepEqual(await getChart("t 1", "sq/1"), { kind: "bar" });
  await getChart("t", "r", "line");
  assert.deepEqual(calls, ["/api/v1/results/t%201/sq%2F1/chart", "/api/v1/results/t/r/chart?kind=line"]);
});

test("getResultPage reads one page by offset and limit", async () => {
  const calls = fakeFetch({ rows: [] });
  await getResultPage("t", "r", 200, 50);
  await getResultPage("t", "r");
  assert.deepEqual(calls, ["/api/v1/results/t/r?offset=200&limit=50", "/api/v1/results/t/r?offset=0&limit=100"]);
});

test("the downloads are the CSV and the Vega-Lite spec, as files with a name", async () => {
  const calls = fakeFetch("a,b\n1,2\n");
  const csv = await downloadCsv("t", "r");
  assert.equal(await csv.blob.text(), "a,b\n1,2\n");
  assert.equal(csv.name, "r.csv");
  const vega = await downloadVegaLite("t", "r", "bar");
  assert.equal(vega.name, "r-vega-lite.json");
  assert.deepEqual(calls, ["/api/v1/results/t/r.csv", "/api/v1/results/t/r/chart?format=vega-lite&kind=bar"]);
});
