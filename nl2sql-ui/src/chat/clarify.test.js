import { test } from "node:test";
import assert from "node:assert/strict";
import { choiceBody, rememberBody } from "./clarify.js";

test("picking a reading sends the option's id as the next turn", () => {
  assert.deepEqual(choiceBody({ id: "b" }, true), { choice: "b", execute: true });
});

test("remembering a reading files the request and option, with the term when there is one", () => {
  const request = { question: "Which?", options: [] };
  assert.deepEqual(rememberBody(request, { id: "b" }, ""), { choice: { request, option_id: "b" } });
  assert.deepEqual(rememberBody(request, { id: "b" }, " revenue "), { choice: { request, option_id: "b" }, term: "revenue" });
});
