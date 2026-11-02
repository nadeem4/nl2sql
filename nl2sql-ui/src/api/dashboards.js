// Dashboards and workspaces (wave_d.md, "Dashboards and workspaces"). Every call is thin over a route of
// routes/dashboards.py or routes/workspaces.py; none makes a model call. The pure parts (the refresh body, the layout)
// are in src/dashboards/.
import { request } from "./http.js";

const enc = encodeURIComponent;

// ---- dashboards
// `status` is "saved" (the server's default) or "draft"; a draft is built in chat and pinned whole.
export function listDashboards({ workspaceId, status } = {}) {
  const query = new URLSearchParams();
  if (workspaceId) query.set("workspace_id", workspaceId);
  if (status) query.set("status", status);
  const text = query.toString();
  return text ? request("GET", `/dashboards?${text}`) : request("GET", "/dashboards");
}

// Only reads: the stored results, when they ran and which tiles are due. The page refreshes the due ones itself.
export const openDashboard = (id) => request("GET", `/dashboards/${enc(id)}`);

// `{tile_id?, filters?, approval_ids?}` (filters.js `refreshBody`). Answers a DashboardRun.
export const refreshDashboard = (id, body = {}) => request("POST", `/dashboards/${enc(id)}/refresh`, body);

export const filterValues = (id, filterId, limit = 100) =>
  request("GET", `/dashboards/${enc(id)}/filters/${enc(filterId)}/values?limit=${limit}`);

export const renameDashboard = (id, title) => request("PUT", `/dashboards/${enc(id)}/title`, { title });

export const renameTile = (id, tileId, title) =>
  request("PUT", `/dashboards/${enc(id)}/tiles/${enc(tileId)}/title`, { title });

// `chart` is auto, line, bar, big_number or table.
export const setTileChart = (id, tileId, chart) =>
  request("PUT", `/dashboards/${enc(id)}/tiles/${enc(tileId)}/chart`, { chart });

export const removeTile = (id, tileId) => request("DELETE", `/dashboards/${enc(id)}/tiles/${enc(tileId)}`);

export const arrange = (id, arrangements) => request("PUT", `/dashboards/${enc(id)}/layout`, { arrangements });

export const setFilters = (id, filters) => request("PUT", `/dashboards/${enc(id)}/filters`, { filters });

// `null` stops the schedule.
export const setSchedule = (id, everyMinutes) =>
  request("PUT", `/dashboards/${enc(id)}/schedule`, { every_minutes: everyMinutes });

export function pinDashboard(id, { workspaceId, title } = {}) {
  const body = {};
  if (workspaceId) body.workspace_id = workspaceId;
  if (title) body.title = title;
  return request("POST", `/dashboards/${enc(id)}/pin`, body);
}

export const moveDashboard = (id, workspaceId) =>
  request("POST", `/dashboards/${enc(id)}/move`, { workspace_id: workspaceId });

export const deleteDashboard = (id) => request("DELETE", `/dashboards/${enc(id)}`);

// ---- workspaces
export const listWorkspaces = () => request("GET", "/workspaces");

export const createWorkspace = (name) => request("POST", "/workspaces", { name });

export const renameWorkspace = (id, name) => request("PUT", `/workspaces/${enc(id)}/name`, { name });

// An empty workspace only; a non-empty one is a 422 (WorkspaceNotEmpty).
export const deleteWorkspace = (id) => request("DELETE", `/workspaces/${enc(id)}`);
