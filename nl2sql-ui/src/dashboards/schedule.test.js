import { test } from "node:test";
import assert from "node:assert/strict";
import { everyLabel, parseMinutes, scheduleNote } from "./schedule.js";

test("an interval reads in minutes, hours or days", () => {
  assert.equal(everyLabel(30), "every 30 min");
  assert.equal(everyLabel(60), "every hour");
  assert.equal(everyLabel(120), "every 2 h");
  assert.equal(everyLabel(1440), "every day");
  assert.equal(everyLabel(90), "every 90 min");
});

test("parseMinutes takes a whole number of at least one and nothing else", () => {
  assert.equal(parseMinutes("15"), 15);
  assert.equal(parseMinutes(" 15 "), 15);
  for (const bad of ["", "0", "-5", "1.5", "abc"]) assert.equal(parseMinutes(bad), null, bad);
});

test("the note says whether a schedule is running and why it stopped", () => {
  assert.equal(scheduleNote(null), "No schedule.");
  assert.equal(scheduleNote({ every_minutes: 60, state: "active" }), "Refreshes every hour.");
  assert.equal(
    scheduleNote({ every_minutes: 60, state: "stopped", error: { message: "The key was revoked." } }),
    "Stopped: The key was revoked.",
  );
});
