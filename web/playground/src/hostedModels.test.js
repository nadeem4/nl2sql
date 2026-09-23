// Run with `npm test` (node's built-in runner; no test dependency).
import { test } from "node:test";
import assert from "node:assert/strict";
import { MODELS_STORE, chosenCount, providersNeeded, readModels, writeModel } from "./hostedModels.js";

function fakeStorage(initial = {}) {
  const store = { ...initial };
  return {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => {
      store[k] = v;
    },
    removeItem: (k) => {
      delete store[k];
    },
    store,
  };
}

function brokenStorage() {
  return {
    getItem() {
      throw new Error("site data blocked");
    },
    setItem() {
      throw new Error("site data blocked");
    },
    removeItem() {
      throw new Error("site data blocked");
    },
  };
}

test("a step's choice is kept in this tab's storage and read back", () => {
  const storage = fakeStorage();

  const models = writeModel(storage, {}, "astplanner", "anthropic:claude-opus-5");

  assert.deepEqual(models, { astplanner: "anthropic:claude-opus-5" });
  assert.deepEqual(readModels(storage), { astplanner: "anthropic:claude-opus-5" });
});

test("back to the default removes the step rather than storing an empty choice", () => {
  const storage = fakeStorage();
  const one = writeModel(storage, {}, "astplanner", "openai:gpt-4.1");

  const none = writeModel(storage, one, "astplanner", "");

  assert.deepEqual(none, {});
  assert.equal(MODELS_STORE in storage.store, false);
  assert.deepEqual(readModels(storage), {});
});

test("nothing but provider:model is ever stored or sent", () => {
  const storage = fakeStorage();

  const kept = writeModel(storage, {}, "astplanner", "not a choice");

  assert.deepEqual(kept, {});
  assert.deepEqual(readModels(fakeStorage({ [MODELS_STORE]: '{"astplanner": 7}' })), {});
  assert.deepEqual(readModels(fakeStorage({ [MODELS_STORE]: "not json" })), {});
  assert.deepEqual(readModels(fakeStorage({ [MODELS_STORE]: "[1,2]" })), {});
});

test("storage that throws leaves the page working", () => {
  const storage = brokenStorage();

  assert.deepEqual(readModels(storage), {});
  assert.deepEqual(writeModel(storage, {}, "refiner", "openai:gpt-4.1"),
    { refiner: "openai:gpt-4.1" });
});

test("the providers the choices need are named once each", () => {
  const models = { astplanner: "anthropic:claude-opus-5", refiner: "anthropic:claude-haiku-4-5",
    decomposer: "openai:gpt-4.1" };

  assert.deepEqual(providersNeeded(models), ["anthropic", "openai"]);
  assert.deepEqual(providersNeeded({}), []);
  assert.deepEqual(providersNeeded(null), []);
});

test("the count is how many steps are off the default", () => {
  assert.equal(chosenCount({ astplanner: "openai:gpt-4.1" }), 1);
  assert.equal(chosenCount({}), 0);
  assert.equal(chosenCount(null), 0);
});
