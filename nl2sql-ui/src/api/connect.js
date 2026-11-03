// Connect: the datasource routes. Admin-only calls answer 403 to everyone else, and always on the hosted demo.
import { request } from "./http.js";

const id = (value) => encodeURIComponent(value);

export const listTypes = () => request("GET", "/datasource-types");
export const testConnection = (config) => request("POST", "/datasources/test", { config });
export const addDatasource = (config, save) => request("POST", "/datasources", { config, save });
export const listDatasources = () => request("GET", "/datasources");
export const removeDatasource = (name) => request("DELETE", `/datasources/${id(name)}`);
export const getSchema = (name) => request("GET", `/datasources/${id(name)}/schema`);
export const starterQuestions = (name) => request("GET", `/datasources/${id(name)}/starter-questions`);

// The jobs follow with `followJob` in jobs.js. Profiling with `draft` asks the model for proposals and needs a cap.
export const startIndex = (datasourceIds) => request("POST", "/jobs/index", { datasource_ids: datasourceIds });
export const startProfile = (datasourceIds, draft, maxCostUsd) =>
  request("POST", "/jobs/profile", draft
    ? { datasource_ids: datasourceIds, draft: true, max_cost_usd: maxCostUsd }
    : { datasource_ids: datasourceIds });

// Starter questions the person kept, filed as example proposals for review.
export const fileExamples = (datasourceId, questions) =>
  request("POST", "/proposals/examples", { datasource_id: datasourceId, questions });
