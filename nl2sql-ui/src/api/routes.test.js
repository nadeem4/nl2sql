// Every server path a client file in src/api names must be a route of the API's committed OpenAPI contract, with the
// method it uses. A route renamed or removed in packages/api fails here, not in a browser.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";

const folder = new URL("./", import.meta.url);
const contract = JSON.parse(readFileSync(new URL("../../../packages/api/openapi.json", import.meta.url), "utf8"));
const BASE = "/api/v1";

// [method, path] for each request("METHOD", "/path") and stream("/path", ...) in a source text. A stream is a GET with
// no body and a POST with one, so it names its method in a `// GET` or `// POST` comment on its line.
function callsIn(source) {
  const found = [];
  for (const m of source.matchAll(/\brequest\(\s*"([A-Z]+)"\s*,\s*([`"])([^`"]+)\2/g)) found.push([m[1], m[3]]);
  for (const m of source.matchAll(/\bstream\(\s*([`"])([^`"]+)\1[^\n]*\/\/\s*(GET|POST)\b/g)) found.push([m[3], m[2]]);
  return found.map(([method, path]) => [method, path.replace(/\$\{[^}]*\}/g, "{}").replace(/\?.*$/, "")]);
}

// An OpenAPI path with every {name} read as {}, to compare with a call's.
const contractPaths = new Map(
  Object.entries(contract.paths).map(([path, operations]) => [path.replace(/\{[^}]*\}/g, "{}"), operations]),
);

test("callsIn reads the calls a client file makes", () => {
  const source = [
    'request("GET", `/jobs/${id}`);',
    'request("POST", "/jobs/index", body);',
    "stream(`/jobs/${id}/events`, undefined, onEvent); // GET",
    'request("GET", `/dashboards?workspace=${w}`);',
  ].join("\n");
  assert.deepEqual(callsIn(source), [
    ["GET", "/jobs/{}"], ["POST", "/jobs/index"], ["GET", "/dashboards"], ["GET", "/jobs/{}/events"],
  ]);
});

test("the contract's paths are written under /api/v1", () => {
  assert.ok(contractPaths.has(`${BASE}/jobs/{}`));
});

for (const file of readdirSync(folder).filter((f) => f.endsWith(".js") && !f.endsWith(".test.js"))) {
  for (const [method, path] of callsIn(readFileSync(new URL(file, folder), "utf8"))) {
    test(`${file}: ${method} ${path} is a route of the API`, () => {
      const operations = contractPaths.get(`${BASE}${path}`);
      assert.ok(operations, `${BASE}${path} is not in packages/api/openapi.json`);
      assert.ok(operations[method.toLowerCase()], `${BASE}${path} has no ${method}`);
    });
  }
}
