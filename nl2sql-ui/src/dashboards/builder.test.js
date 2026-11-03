import { test } from "node:test";
import assert from "node:assert/strict";
import * as api from "../api/dashboards.js";
import * as jobs from "../api/jobs.js";
import { fakeApi } from "./fakeApi.js";
import {
  askedBlock, buildBody, buildFlow, canAdvance, describeBuilderError, emptyState, proposalsBody, reword,
  skippedLines, sourceChoices, tick, toggle, withTiles, CHARTS, canReword, effectiveState, setChart,
} from "./builder.js";

test("a chip toggles in and out of a list without touching the original", () => {
  const list = ["a"];
  assert.deepEqual(toggle(list, "b"), ["a", "b"]);
  assert.deepEqual(toggle(list, "a"), []);
  assert.deepEqual(list, ["a"]);
});

test("the three sources are offered, and the model one is held closed with its reason when there is no key", () => {
  const open = sourceChoices(null);
  assert.deepEqual(open.map((c) => c.id), ["curated", "saved", "asked"]);
  assert.ok(open.every((c) => !c.disabled));
  const shut = sourceChoices("Add your API key under Settings first.");
  assert.equal(shut[2].disabled, true);
  assert.equal(shut[2].reason, "Add your API key under Settings first.");
  assert.ok(!shut[0].disabled && !shut[1].disabled);
});

test("only a hosted visitor without a key is blocked from the model source", () => {
  assert.ok(askedBlock({ mode: "hosted" }, {}));
  assert.equal(askedBlock({ mode: "hosted" }, { anthropic: "sk-x" }), null);
  assert.equal(askedBlock({ mode: "demo" }, {}), null);
  assert.equal(askedBlock(null, {}), null);
});

test("a new builder starts on the curated source for one datasource", () => {
  const state = emptyState("chinook");
  assert.equal(state.datasourceId, "chinook");
  assert.deepEqual(state.sources, ["curated"]);
});

test("a step can be left only when it has what the next needs", () => {
  const base = emptyState("chinook");
  assert.equal(canAdvance("sources", { ...base, sources: [] }), false);
  assert.equal(canAdvance("sources", base), true);
  // inputs: curated and saved need a chosen question; the model source needs a chip or some words
  assert.equal(canAdvance("inputs", base), false);
  assert.equal(canAdvance("inputs", { ...base, curatedIds: ["v1"] }), true);
  assert.equal(canAdvance("inputs", { ...base, sources: ["saved"] }), false);
  assert.equal(canAdvance("inputs", { ...base, sources: ["saved"], savedIds: ["a1"] }), true);
  assert.equal(canAdvance("inputs", { ...base, sources: ["asked"] }), false);
  assert.equal(canAdvance("inputs", { ...base, sources: ["asked"], text: "  " }), false);
  assert.equal(canAdvance("inputs", { ...base, sources: ["asked"], text: "returns" }), true);
  assert.equal(canAdvance("inputs", { ...base, sources: ["asked"], metrics: ["Revenue"] }), true);
  // review: at least one ticked tile
  assert.equal(canAdvance("review", { ...base, tiles: [] }), false);
  assert.equal(canAdvance("review", { ...base, tiles: [{ ticked: false }] }), false);
  assert.equal(canAdvance("review", { ...base, tiles: [{ ticked: true }] }), true);
});

test("the proposals body leaves out what was not chosen", () => {
  const state = {
    ...emptyState("chinook"), sources: ["curated", "asked"], curatedIds: ["v1"], savedIds: ["a1"],
    metrics: ["Revenue"], dimensions: ["chinook.main.invoice.country"], timeRange: "last_30_days", text: " by region ",
  };
  assert.deepEqual(proposalsBody(state), {
    datasource_id: "chinook", sources: ["curated", "asked"], curated_ids: ["v1"], saved_ids: [],
    metrics: ["Revenue"], dimensions: ["chinook.main.invoice.country"], time_range: "last_30_days", text: "by region",
  });
  assert.deepEqual(proposalsBody({ ...emptyState("a"), curatedIds: ["v1"] }), {
    datasource_id: "a", sources: ["curated"], curated_ids: ["v1"], saved_ids: [], metrics: [], dimensions: [],
  });
});

test("model inputs are not sent when the model source is not chosen", () => {
  const body = proposalsBody({ ...emptyState("a"), curatedIds: ["v1"], metrics: ["Revenue"], text: "hello", timeRange: "this_year" });
  assert.deepEqual(body.metrics, []);
  assert.equal(body.text, undefined);
  assert.equal(body.time_range, undefined);
});

test("ticking and rewording change one tile; rewording clears the made title", () => {
  const tiles = [
    { source: "asked", ref: null, question: "A?", title: "A", chart: "auto", ticked: true },
    { source: "curated", ref: "v1", question: "B?", title: "B", chart: "auto", ticked: true },
  ];
  assert.equal(tick(tiles, 1, false)[1].ticked, false);
  assert.equal(tick(tiles, 1, false)[0].ticked, true);
  const worded = reword(tiles, 0, "A better?");
  assert.equal(worded[0].question, "A better?");
  assert.equal(worded[0].title, "");
  assert.equal(tiles[0].question, "A?");
});

test("the build body sends every tile, only the set fields", () => {
  const tiles = [{ source: "curated", ref: "v1", question: "B?", title: "B", chart: "auto", ticked: true }];
  assert.deepEqual(buildBody({ ...emptyState("chinook"), tiles }), { datasource_id: "chinook", tiles });
  assert.deepEqual(
    buildBody({ ...emptyState("chinook"), tiles, title: " Sales ", workspaceId: "w1", timeColumn: "chinook.main.invoice.date" }),
    { datasource_id: "chinook", tiles, title: "Sales", workspace_id: "w1", time_column: "chinook.main.invoice.date" },
  );
});

test("withTiles swaps the review list and keeps what was chosen", () => {
  const next = withTiles({ ...emptyState("a"), text: "x" }, [{ ticked: true }]);
  assert.deepEqual(next.tiles, [{ ticked: true }]);
  assert.equal(next.text, "x");
});

test("skipped questions read plainly, with the reason", () => {
  assert.deepEqual(
    skippedLines([{ question: "Churn?", code: "NOT_BUILT", message: "No column holds churn." }]),
    ["Couldn't answer \"Churn?\": No column holds churn."],
  );
  assert.deepEqual(skippedLines([]), []);
  assert.deepEqual(skippedLines(undefined), []);
});

test("errors read in one sentence; a missing key points to Settings", () => {
  assert.match(describeBuilderError({ code: "MISSING_PROVIDER_KEY", message: "no key" }), /Settings/);
  assert.match(describeBuilderError({ status: 409, message: "x" }, "build"), /already have a job running/);
  assert.doesNotMatch(describeBuilderError({ status: 409, code: "CONFLICT", message: "Changed meanwhile." }), /job running/);
  assert.equal(describeBuilderError({ code: "NO_TILES", message: "Nothing was built.", fix: "Pick others." }), "Nothing was built. Pick others.");
});

const queued = {
  id: "job1", kind: "dashboard-build", status: "queued", params: {}, created_by: "ann", created_at: "t", started_at: null,
  finished_at: null, progress: [], result: null, error: null,
};
const made = { id: "d9", workspace_id: "w2", title: "Mine" };
const report = { dashboard: made, built: ["t1"], skipped: [{ question: "Q?", code: "X", message: "no" }] };
const proposed = [{ source: "asked", ref: null, question: "Q?", title: "Q", chart: "auto", ticked: true }];

test("proposals, then the build job, end with the report and the dashboard to open", async () => {
  const calls = fakeApi((method, path) => {
    if (path === "/dashboard-builder/proposals") return proposed;
    if (path === "/jobs/dashboard-build") return queued;
    return { ...queued, status: "succeeded", result: report };
  });
  const found = await api.proposeTiles(proposalsBody({ ...emptyState("chinook"), sources: ["asked"], text: "x" }));
  assert.equal(calls[0].method, "POST");
  assert.equal(calls[0].path, "/dashboard-builder/proposals");
  assert.equal(found.length, 1);
  const seen = [];
  const out = await buildFlow({ startJob: api.startBuildJob, follow: jobs.followJob }, { ...emptyState("chinook"), tiles: found },
    { onJob: (j) => seen.push(j.status) });
  assert.equal(calls[1].path, "/jobs/dashboard-build");
  assert.equal(calls[1].body.datasource_id, "chinook");
  assert.equal(out.hash, "#/dashboards/d9");
  assert.deepEqual(out.report, report);
  assert.equal(seen[0], "queued");
  assert.equal(seen[seen.length - 1], "succeeded");
});

test("the options call is a GET with the datasource", async () => {
  const calls = fakeApi({});
  await api.builderOptions("chinook");
  assert.equal(calls[0].method, "GET");
  assert.equal(calls[0].path, "/dashboard-builder/options?datasource_id=chinook");
});

test("progress steps reach the page as they come", async () => {
  const counts = [];
  const step = { at: "t", step: "tile 1 of 2", done: 1, total: 2 };
  await buildFlow(
    { startJob: async () => queued, follow: async (id, on) => { on(step); return { ...queued, status: "succeeded", result: report }; } },
    emptyState("a"), { onJob: (j) => counts.push(j.progress.length) },
  );
  assert.deepEqual(counts, [0, 1, 0]);
});

test("a failed build job throws its error, with the code", async () => {
  const failed = { ...queued, status: "failed", error: { code: "MISSING_PROVIDER_KEY", message: "No key.", fix: "Add one." } };
  await assert.rejects(
    buildFlow({ startJob: async () => queued, follow: async () => failed }, emptyState("a")),
    (e) => e.code === "MISSING_PROVIDER_KEY" && e.fix === "Add one.",
  );
});

test("a cancelled or aborted build opens nothing", async () => {
  const cancelled = { ...queued, status: "cancelled" };
  assert.deepEqual(await buildFlow({ startJob: async () => queued, follow: async () => cancelled }, emptyState("a")), { cancelled: true });
  const controller = new AbortController();
  controller.abort();
  assert.deepEqual(
    await buildFlow({ startJob: async () => queued, follow: async () => cancelled }, emptyState("a"), { signal: controller.signal }),
    { aborted: true },
  );
});

test("only a new (asked) tile can be reworded; curated and saved tiles keep their question", () => {
  assert.equal(canReword({ source: "asked" }), true);
  assert.equal(canReword({ source: "curated" }), false);
  assert.equal(canReword({ source: "saved" }), false);
});

test("a tile's chart is set to one of the kinds the API accepts, and the others stay as they were", () => {
  assert.deepEqual(CHARTS, ["auto", "line", "bar", "big_number", "table"]);
  const tiles = [{ chart: "auto" }, { chart: "bar" }];
  const out = setChart(tiles, 0, "line");
  assert.equal(out[0].chart, "line");
  assert.equal(out[1].chart, "bar");
  assert.equal(tiles[0].chart, "auto");
  assert.equal(setChart(tiles, 0, "pie"), tiles);
});

test("the model source is dropped when it is blocked, and kept when it is not", () => {
  const state = { ...emptyState("d"), sources: ["curated", "asked"] };
  assert.deepEqual(effectiveState(state, "Needs a key").sources, ["curated"]);
  assert.equal(effectiveState(state, null), state);
});
