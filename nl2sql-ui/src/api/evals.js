// Evals: the suites and scorers the server knows, and the job that runs them (A4a). Follow the job with `followJob`
// in jobs.js.
import { request } from "./http.js";

export const listSuites = () => request("GET", "/evals/suites");
export const listScorers = () => request("GET", "/evals/scorers");

// Only what was chosen is sent: no suites or no cases means all of them. `maxCostUsd` caps a run that calls the model.
export function startEval({ mode, suites, cases, maxCostUsd }) {
  const body = { mode };
  if (suites && suites.length) body.suites = suites;
  if (cases && cases.length) body.cases = cases;
  if (maxCostUsd !== undefined && maxCostUsd !== null) body.max_cost_usd = maxCostUsd;
  return request("POST", "/jobs/eval", body);
}
