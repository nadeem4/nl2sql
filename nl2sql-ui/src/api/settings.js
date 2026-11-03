// Settings: the server's LLM, the providers it knows, and the API keys it issues (A4a). Admin calls answer 403 to
// anyone else; the page asks /meta first and does not offer them.
import { request } from "./http.js";

export const getLlm = () => request("GET", "/llm");
export const getProviders = () => request("GET", "/llm/providers");
export const getIndex = () => request("GET", "/index");

// Replaces the whole `llm` section in memory (nothing is written to a file).
export const replaceLlm = (section) => request("PUT", "/settings/llm", section);

export const listApiKeys = () => request("GET", "/api-keys");
export function createApiKey({ userId, roles, tenantId, label }) {
  const body = { user_id: userId, roles };
  if (tenantId) body.tenant_id = tenantId;
  if (label) body.label = label;
  return request("POST", "/api-keys", body);
}
export const revokeApiKey = (id) => request("DELETE", `/api-keys/${encodeURIComponent(id)}`);
