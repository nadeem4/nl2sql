import { test } from "node:test";
import assert from "node:assert/strict";
import {
  arrange, deleteDashboard, filterValues, openDashboard, refreshDashboard, removeTile, renameDashboard, renameTile,
  setFilters, setSchedule, setTileChart,
} from "../api/dashboards.js";
import { fakeApi } from "./fakeApi.js";

test("opening only reads, and a refresh posts its body", async () => {
  const calls = fakeApi();
  await openDashboard("d 1");
  await refreshDashboard("d1", { tile_id: "t1", filters: { f1: ["US"] } });
  assert.deepEqual(calls, [
    { method: "GET", path: "/dashboards/d%201", body: undefined },
    { method: "POST", path: "/dashboards/d1/refresh", body: { tile_id: "t1", filters: { f1: ["US"] } } },
  ]);
});

test("rename, chart type and remove call the routes of the tile", async () => {
  const calls = fakeApi();
  await renameDashboard("d1", "Sales");
  await renameTile("d1", "t1", "Revenue");
  await setTileChart("d1", "t1", "line");
  await removeTile("d1", "t2");
  assert.deepEqual(calls, [
    { method: "PUT", path: "/dashboards/d1/title", body: { title: "Sales" } },
    { method: "PUT", path: "/dashboards/d1/tiles/t1/title", body: { title: "Revenue" } },
    { method: "PUT", path: "/dashboards/d1/tiles/t1/chart", body: { chart: "line" } },
    { method: "DELETE", path: "/dashboards/d1/tiles/t2", body: undefined },
  ]);
});

test("arrange sends the TileArrangements, filters and schedule send theirs", async () => {
  const calls = fakeApi();
  const arrangements = [{ tile_id: "t1", x: 0, y: 0, w: 2, h: 2 }];
  await arrange("d1", arrangements);
  await setFilters("d1", []);
  await setSchedule("d1", 60);
  await setSchedule("d1", null);
  assert.deepEqual(calls.map((c) => [c.method, c.path, c.body]), [
    ["PUT", "/dashboards/d1/layout", { arrangements }],
    ["PUT", "/dashboards/d1/filters", { filters: [] }],
    ["PUT", "/dashboards/d1/schedule", { every_minutes: 60 }],
    ["PUT", "/dashboards/d1/schedule", { every_minutes: null }],
  ]);
});

test("filter values are read with a limit, and a dashboard is deleted by id", async () => {
  const calls = fakeApi();
  await filterValues("d1", "f 1", 50);
  await deleteDashboard("d1");
  assert.deepEqual(calls.map((c) => [c.method, c.path]), [
    ["GET", "/dashboards/d1/filters/f%201/values?limit=50"],
    ["DELETE", "/dashboards/d1"],
  ]);
});
