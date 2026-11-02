// Knowledge: what the engine knows, the proposals waiting for review, and the drift check (A3).
import { request } from "./http.js";

export const showKnowledge = (datasourceId) =>
  request("GET", `/knowledge?${new URLSearchParams(datasourceId ? { datasource_id: datasourceId } : {})}`);

export const listProposals = (datasourceId) => {
  const qs = new URLSearchParams({ status: "proposed", limit: "200" });
  if (datasourceId) qs.set("datasource_id", datasourceId);
  return request("GET", `/proposals?${qs}`);
};
export const acceptProposals = (body) => request("POST", "/proposals/accept", body);
export const rejectProposals = (body) => request("POST", "/proposals/reject", body);

export const checkDrift = (datasourceIds) => {
  const qs = new URLSearchParams();
  for (const d of datasourceIds || []) qs.append("datasource_id", d);
  return request("GET", `/index/check?${qs}`);
};
