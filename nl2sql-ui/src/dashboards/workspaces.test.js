import { test } from "node:test";
import assert from "node:assert/strict";
import {
  createWorkspace, deleteWorkspace, listDashboards, listWorkspaces, moveDashboard, pinDashboard, renameWorkspace,
} from "../api/dashboards.js";
import { fakeApi } from "./fakeApi.js";

test("workspaces are listed, created, renamed and deleted", async () => {
  const calls = fakeApi();
  await listWorkspaces();
  await createWorkspace("Finance");
  await renameWorkspace("w 1", "Ops");
  await deleteWorkspace("w1");
  assert.deepEqual(calls.map((c) => [c.method, c.path, c.body]), [
    ["GET", "/workspaces", undefined],
    ["POST", "/workspaces", { name: "Finance" }],
    ["PUT", "/workspaces/w%201/name", { name: "Ops" }],
    ["DELETE", "/workspaces/w1", undefined],
  ]);
});

test("dashboards are listed for a workspace, or as drafts", async () => {
  const calls = fakeApi([]);
  await listDashboards({ workspaceId: "w1" });
  await listDashboards({ status: "draft" });
  await listDashboards();
  assert.deepEqual(calls.map((c) => c.path), [
    "/dashboards?workspace_id=w1", "/dashboards?status=draft", "/dashboards",
  ]);
});

test("a dashboard moves to a workspace, and a draft is pinned whole into one", async () => {
  const calls = fakeApi();
  await moveDashboard("d1", "w2");
  await pinDashboard("d1", { workspaceId: "w2", title: "Sales" });
  await pinDashboard("d1", {});
  assert.deepEqual(calls.map((c) => [c.method, c.path, c.body]), [
    ["POST", "/dashboards/d1/move", { workspace_id: "w2" }],
    ["POST", "/dashboards/d1/pin", { workspace_id: "w2", title: "Sales" }],
    ["POST", "/dashboards/d1/pin", {}],
  ]);
});
