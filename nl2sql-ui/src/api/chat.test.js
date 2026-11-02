// The bodies the chat client sends, pinned to what the server reads (routes/conversations.py, approvals.py,
// knowledge.py, query.py).
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  correct, denyApproval, grantApproval, rate, retryTurn, runTool, saveChoice, sendTurn, startConversation,
} from "./chat.js";

let calls;
function fakeFetch() {
  calls = [];
  globalThis.sessionStorage = { getItem: () => null, setItem() {}, removeItem() {} };
  globalThis.fetch = async (url, init) => {
    calls.push({ url: String(url), method: init.method, body: init.body ? JSON.parse(init.body) : undefined });
    return new Response("{}", { status: 200, headers: { "content-type": "application/json" } });
  };
}

test("a question and a clarification pick are turns", async () => {
  fakeFetch();
  await sendTurn("c 1", { text: "How many?", execute: true });
  await sendTurn("c 1", { choice: "a", execute: true });
  assert.deepEqual(calls, [
    { url: "/api/v1/conversations/c%201/turns", method: "POST", body: { text: "How many?", execute: true } },
    { url: "/api/v1/conversations/c%201/turns", method: "POST", body: { choice: "a", execute: true } },
  ]);
});

test("a new conversation sends the datasource when it has one", async () => {
  fakeFetch();
  await startConversation({ datasource_id: "chinook" });
  assert.deepEqual(calls[0].body, { datasource_id: "chinook" });
});

test("a retry names the granted requests", async () => {
  fakeFetch();
  await retryTurn("c1", ["ap-1"]);
  assert.deepEqual(calls[0], { url: "/api/v1/conversations/c1/retry", method: "POST", body: { approval_ids: ["ap-1"] } });
});

test("a tool call wraps its arguments", async () => {
  fakeFetch();
  await runTool("c1", "chart", { turn: 2, step: 1 });
  assert.deepEqual(calls[0], { url: "/api/v1/conversations/c1/tools/chart", method: "POST", body: { args: { turn: 2, step: 1 } } });
});

test("a decision sends a reason only when there is one", async () => {
  fakeFetch();
  await denyApproval("ap-1");
  await denyApproval("ap-1", "too broad");
  await grantApproval("ap-1");
  assert.deepEqual(calls.map((c) => [c.url, c.body]), [
    ["/api/v1/approvals/ap-1/deny", {}],
    ["/api/v1/approvals/ap-1/deny", { reason: "too broad" }],
    ["/api/v1/approvals/ap-1/grant", {}],
  ]);
});

test("a rating, a correction and a remembered choice go to their routes", async () => {
  fakeFetch();
  await rate({ trace_id: "t", rating: "up" });
  await correct({ trace_id: "t", sql: "SELECT 1", note: "n" });
  await saveChoice({ choice: { request: { id: "r" }, option_id: "a" }, term: "sales" });
  assert.deepEqual(calls.map((c) => [c.url, c.body]), [
    ["/api/v1/feedback", { trace_id: "t", rating: "up" }],
    ["/api/v1/feedback/correct", { trace_id: "t", sql: "SELECT 1", note: "n" }],
    ["/api/v1/knowledge/choices", { choice: { request: { id: "r" }, option_id: "a" }, term: "sales" }],
  ]);
});
