// The calls the Settings page makes, pinned to what routes/settings.py, health.py and index.py read.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  createApiKey, getIndex, getLlm, getProviders, listApiKeys, revokeApiKey, setModels,
} from "./settings.js";

let calls;
function fakeFetch(status = 200, answer = "{}") {
  calls = [];
  globalThis.sessionStorage = { getItem: () => null, setItem() {}, removeItem() {} };
  globalThis.fetch = async (url, init) => {
    calls.push({ url: String(url), method: init.method, body: init.body ? JSON.parse(init.body) : undefined });
    return status === 204
      ? new Response(null, { status })
      : new Response(answer, { status, headers: { "content-type": "application/json" } });
  };
}

test("the reads", async () => {
  fakeFetch();
  await getLlm();
  await getProviders();
  await listApiKeys();
  await getIndex();
  assert.deepEqual(calls.map((c) => [c.method, c.url]), [
    ["GET", "/api/v1/llm"], ["GET", "/api/v1/llm/providers"], ["GET", "/api/v1/api-keys"], ["GET", "/api/v1/index"],
  ]);
});

test("getLlm unwraps the agents from the route's {llms: ...} body", async () => {
  fakeFetch(200, JSON.stringify({ llms: { default: { provider: "openai", model: "m", temperature: 0, inherited: false } } }));
  const llms = await getLlm();
  assert.deepEqual(Object.keys(llms), ["default"]);
  assert.equal(llms.default.model, "m");
});

test("changing models patches only the changes", async () => {
  fakeFetch();
  await setModels({ default: { provider: "openai", model: "m" }, refiner: null });
  assert.deepEqual(calls[0], {
    url: "/api/v1/settings/llm", method: "PATCH", body: { default: { provider: "openai", model: "m" }, refiner: null },
  });
});

test("a new API key names the user, the roles, the tenant and the label", async () => {
  fakeFetch(201);
  await createApiKey({ userId: "ann", roles: ["analyst"], tenantId: "acme", label: "ci" });
  await createApiKey({ userId: "bob", roles: [] });
  assert.deepEqual(calls.map((c) => c.body), [
    { user_id: "ann", roles: ["analyst"], tenant_id: "acme", label: "ci" },
    { user_id: "bob", roles: [] },
  ]);
});

test("revoking an API key is a delete of its id", async () => {
  fakeFetch(204);
  assert.equal(await revokeApiKey("k 1"), null);
  assert.deepEqual([calls[0].method, calls[0].url], ["DELETE", "/api/v1/api-keys/k%201"]);
});
