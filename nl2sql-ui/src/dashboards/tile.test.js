import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { ageLabel, refreshedLabel, runsById, tileState } from "./tile.js";

const view = JSON.parse(readFileSync(new URL("../api/fixtures/DashboardView.json", import.meta.url), "utf8"));
const runs = runsById(view.runs);

test("an age reads in the largest unit that fits", () => {
  const at = "2026-01-02T03:04:05Z";
  const later = (ms) => new Date(Date.parse(at) + ms);
  assert.equal(ageLabel(at, later(20_000)), "just now");
  assert.equal(ageLabel(at, later(5 * 60_000)), "5 min ago");
  assert.equal(ageLabel(at, later(3 * 3_600_000)), "3 h ago");
  assert.equal(ageLabel(at, later(2 * 86_400_000)), "2 days ago");
  assert.equal(ageLabel(null, later(0)), "");
  assert.equal(ageLabel("not a date", later(0)), "");
});

test("the dashboard says how old its oldest result is", () => {
  const now = new Date(Date.parse(view.refreshed_at) + 10 * 60_000);
  assert.equal(refreshedLabel(view.refreshed_at, now), "Refreshed 10 min ago");
  assert.equal(refreshedLabel(null, now), "Not refreshed yet");
});

test("a tile with a chart shows it", () => {
  const state = tileState(runs.get("t1"));
  assert.equal(state.kind, "chart");
  assert.equal(state.spec.kind, "bar");
});

test("needs_fixing says what broke and offers removal", () => {
  const state = tileState(runs.get("t2"));
  assert.equal(state.kind, "fix");
  assert.equal(state.message, "A column it reads is gone.");
  assert.equal(state.removable, true);
});

test("denied says the caller may not read it, with the server's reason", () => {
  const state = tileState(runs.get("t3"));
  assert.equal(state.kind, "denied");
  assert.equal(state.message, "You may not read this.");
});

test("needs_approval names the reason and carries the request ids", () => {
  const state = tileState(runs.get("t4"));
  assert.equal(state.kind, "approval");
  assert.equal(state.message, "Reads a lot.");
  assert.equal(state.approvalIds.length, 1);
});

test("a tile with no run yet, an empty result, a stop and an error each have a state", () => {
  assert.equal(tileState(undefined).kind, "pending");
  assert.equal(tileState({ status: "empty", chart: null }).kind, "empty");
  assert.equal(tileState({ status: "stopped", chart: null }).kind, "stopped");
  const error = tileState({ status: "error", chart: null, error: { code: "X", message: "It broke." } });
  assert.deepEqual([error.kind, error.message], ["error", "It broke."]);
});
