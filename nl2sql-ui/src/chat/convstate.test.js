import { test } from "node:test";
import assert from "node:assert/strict";
import { addressChange, withConversation, withTurn } from "./convstate.js";

const conv = (id, turns = []) => ({ id, turns });

test("a finished turn lands only in the conversation it was sent for", () => {
  assert.deepEqual(withTurn(conv("a"), "a", { index: 1 }).turns, [{ index: 1 }]);
  const other = conv("b");
  assert.equal(withTurn(other, "a", { index: 1 }), other);
  assert.equal(withTurn(null, "a", { index: 1 }), null);
});

test("a turn is not added twice", () => {
  const c = conv("a", [{ index: 1 }]);
  assert.equal(withTurn(c, "a", { index: 1 }), c);
});

test("a reloaded conversation replaces only the one on screen", () => {
  const next = conv("a", [{ index: 1 }]);
  assert.equal(withConversation(conv("a"), next), next);
  assert.equal(withConversation(null, next), null);
  const other = conv("b");
  assert.equal(withConversation(other, next), other);
});

test("the address a page just made is not loaded over its stream, once", () => {
  assert.deepEqual(addressChange("a", "a"), { load: false, created: null });
  assert.deepEqual(addressChange("a", null), { load: true, created: null });
  assert.deepEqual(addressChange("b", "a"), { load: true, created: null });
  assert.deepEqual(addressChange("a", "a"), { load: false, created: null });
});
