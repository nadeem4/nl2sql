// A person's saved answers (wave_d.md, section 18). Every call is thin over a route of routes/answers.py; saving and
// listing call no model, and a run is a normal run as the viewer. Another person's answer is a 404.
import { request } from "./http.js";

const enc = encodeURIComponent;

// `{trace_id, conversation_id, step: {turn?, step}, note?}` (chat/answers.js `saveBody`). Answers a SaveReport.
export const saveAnswer = (body) => request("POST", "/answers", body);

export function listAnswers(datasourceId) {
  return datasourceId ? request("GET", `/answers?datasource_id=${enc(datasourceId)}`) : request("GET", "/answers");
}

export const deleteAnswer = (id) => request("DELETE", `/answers/${enc(id)}`);

// Answers a QueryResult. `approvalIds` are request ids granted in Approvals.
export function runAnswer(id, approvalIds) {
  return approvalIds && approvalIds.length
    ? request("POST", `/answers/${enc(id)}/run`, { approval_ids: approvalIds })
    : request("POST", `/answers/${enc(id)}/run`);
}
