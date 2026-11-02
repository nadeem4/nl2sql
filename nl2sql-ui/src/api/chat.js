// The chat page's calls: conversations and their turns, a turn's rating and correction, the approvals the caller can
// see, and the datasources whose starter questions seed a new conversation. Paths are relative to /api/v1.
import { request, stream } from "./http.js";

const enc = encodeURIComponent;

export const listConversations = (limit = 50) => request("GET", `/conversations?limit=${limit}`);
export const startConversation = (body = {}) => request("POST", "/conversations", body);
export const getConversation = (id) => request("GET", `/conversations/${enc(id)}`);
export const deleteConversation = (id) => request("DELETE", `/conversations/${enc(id)}`);
export const chatTools = () => request("GET", "/chat-tools");

// One turn, stored whole when it ends. `body` is `{text, execute}` or `{choice, execute}`.
export const sendTurn = (id, body) => request("POST", `/conversations/${enc(id)}/turns`, body);

// The same turn as a stream; `onEvent({event, data})` per frame, ending with `conversation.turn` or `run.failed`.
export const streamTurn = (id, body, onEvent, signal) =>
  stream(`/conversations/${enc(id)}/turns/stream`, body, onEvent, signal); // POST

// Re-run the turn that waited, now the approvals in `approval_ids` (request ids) are granted.
export const retryTurn = (id, approvalIds) => request("POST", `/conversations/${enc(id)}/retry`, { approval_ids: approvalIds });
export const runTool = (id, name, args) => request("POST", `/conversations/${enc(id)}/tools/${enc(name)}`, { args });

export const rate = (body) => request("POST", "/feedback", body);
export const correct = (body) => request("POST", "/feedback/correct", body);
export const getTrace = (traceId) => request("GET", `/traces/${enc(traceId)}`);

export const listApprovals = (status) => request("GET", `/approvals?status=${enc(status)}`);
export const grantApproval = (id) => request("POST", `/approvals/${enc(id)}/grant`, {});
export const denyApproval = (id, reason) => request("POST", `/approvals/${enc(id)}/deny`, reason ? { reason } : {});

export const listDatasources = () => request("GET", "/datasources");
export const saveChoice = (body) => request("POST", "/knowledge/choices", body);
