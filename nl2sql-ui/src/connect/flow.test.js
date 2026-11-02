// The words and shapes of the Connect flow: what a test, an add and a save say, and which step is current.
import { test } from "node:test";
import assert from "node:assert/strict";
import { ApiError } from "../api/http.js";
import { STEPS, buildConfig, describeAdd, describeTest, fieldErrors, stepStates } from "./flow.js";

test("the config is the datasource's id, type and connection, with a description only when there is one", () => {
  const connection = { host: "db.internal" };
  assert.deepEqual(buildConfig({ id: " sales ", type: "postgres", description: "  ", connection }),
    { id: "sales", type: "postgres", connection });
  assert.deepEqual(buildConfig({ id: "sales", type: "postgres", description: "Orders", connection }),
    { id: "sales", type: "postgres", description: "Orders", connection });
});

test("a passing test says so, and carries the adapter's warnings", () => {
  const out = describeTest({ id: "sales", status: "ready", stage: null, reason: null, fix: null, warnings: ["The session is not read-only."] });
  assert.equal(out.ok, true);
  assert.equal(out.headline, "Connected");
  assert.deepEqual(out.warnings, ["The session is not read-only."]);
});

test("a failed test names the stage in words, the reason and the fix", () => {
  const out = describeTest({ id: "sales", status: "failed", stage: "auth", reason: "Login failed for user.", fix: "Check the password.", warnings: [] });
  assert.equal(out.ok, false);
  assert.equal(out.headline, "Failed at sign-in");
  assert.equal(out.reason, "Login failed for user.");
  assert.equal(out.fix, "Check the password.");
});

test("a failed test with no stage still reads", () => {
  const out = describeTest({ id: "x", status: "failed", stage: null, reason: null, fix: null, warnings: [] });
  assert.equal(out.headline, "The connection failed");
  assert.equal(out.reason, "");
});

test("a 422 lists each problem against its field, without the config. or connection. prefix", () => {
  const error = new ApiError({
    status: 422, code: "INVALID_REQUEST", message: "The datasource does not validate.", fix: "Fix the fields listed in details.",
    details: [{ loc: ["config", "connection", "port"], msg: "Input should be a valid integer" },
      { loc: ["config", "connection", "pool", "size"], msg: "Input should be greater than 0" },
      { loc: ["config", "id"], msg: "String should have at least 1 character" }],
  });
  assert.deepEqual(fieldErrors(error.details), {
    port: "Input should be a valid integer", "pool.size": "Input should be greater than 0", id: "String should have at least 1 character",
  });
  const out = describeAdd(error);
  assert.equal(out.added, false);
  assert.equal(out.fields.port, "Input should be a valid integer");
});

test("a read-only config leaves the datasource added in memory, and says how to keep it", () => {
  const error = new ApiError({ status: 403, code: "NOT_WRITABLE", message: "The datasource could not be written.", fix: "Add it to the mounted config." });
  const out = describeAdd(error);
  assert.equal(out.added, true);
  assert.equal(out.saved, false);
  assert.match(out.message, /until the server restarts/);
  assert.equal(out.fix, "Add it to the mounted config.");
});

test("a literal secret refused for saving is not added, and the fix is shown", () => {
  const error = new ApiError({ status: 422, code: "SECRET_VALUE_IN_CONFIG", message: "A secret would be written into YAML.", fix: "Use an environment reference." });
  const out = describeAdd(error);
  assert.equal(out.added, false);
  assert.equal(out.fix, "Use an environment reference.");
});

test("a taken id is a conflict; anything else is the server's own message", () => {
  const conflict = describeAdd(new ApiError({ status: 409, code: "DATASOURCE_EXISTS", message: "Datasource 'sales' already exists." }));
  assert.equal(conflict.added, false);
  assert.equal(conflict.message, "Datasource 'sales' already exists.");
  const net = describeAdd(new ApiError({ status: 0, code: "NETWORK", message: "The server could not be reached.", fix: "Check it is running." }));
  assert.equal(net.added, false);
  assert.equal(net.fix, "Check it is running.");
});

test("the steps run connection, test, add, index, profile, starter questions, review", () => {
  assert.deepEqual(STEPS.map((s) => s.id), ["connection", "test", "add", "index", "profile", "questions", "review"]);
});

test("steps before the current one are done, the current one is current, the rest are todo", () => {
  assert.deepEqual(stepStates("add").map((s) => s.state), ["done", "done", "current", "todo", "todo", "todo", "todo"]);
  assert.deepEqual(stepStates("connection").map((s) => s.state), ["current", "todo", "todo", "todo", "todo", "todo", "todo"]);
  assert.equal(stepStates("review")[6].state, "current");
});
