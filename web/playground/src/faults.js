// What the fault box says about one `errors[]` entry from `/api/ask`. Pure, so
// `npm test` covers it with node's own test runner.
//
// The engine classifies a provider's refusal (`nl2sql.llm.failures`) into a
// `PROVIDER_*` code and names the provider; this only picks the words and the
// next step. The provider's own text stays in `entry.detail`, for a disclosure.

export const SETTINGS_ROUTE = "#/settings";

// The steps that run before anything is planned. A key refused there cost nothing.
const BEFORE_PLANNING = new Set(["datasource_resolver", "decomposer"]);

const COPY = {
  PROVIDER_AUTH_FAILED: (who, early) => ({
    headline: `${who} rejected this key.`,
    body: early
      ? "Nothing was planned or run, and nothing was charged."
      : "The run stopped at this step. Nothing was charged for the refused call.",
    action: { label: "Replace the key", route: SETTINGS_ROUTE },
  }),
  PROVIDER_RATE_LIMITED: () => ({
    headline: "Rate limited.",
    body: "Wait a minute and ask again.",
    action: null,
  }),
  PROVIDER_QUOTA_EXCEEDED: (who) => ({
    headline: `${who} says this key is out of credit.`,
    body: "Add credit on the provider's billing page, or use another key.",
    action: { label: "Use another key", route: SETTINGS_ROUTE },
  }),
  PROVIDER_TIMEOUT: (who) => ({
    headline: `${who} took too long to answer.`,
    body: "Ask again. If it keeps happening, choose a faster model.",
    action: null,
  }),
  PROVIDER_UNAVAILABLE: (who) => ({
    headline: `${who} is not answering.`,
    body: "The problem is on the provider's side. Try again in a few minutes.",
    action: null,
  }),
  PROVIDER_MODEL_UNAVAILABLE: (who) => ({
    headline: `${who} won't run this model for this key.`,
    body: "Choose another model in Settings.",
    action: { label: "Choose a model", route: SETTINGS_ROUTE },
  }),
};

// `{ headline, body, action }` for one error entry, or null for none. `action`
// is `{ label, route }` or null. An unknown code shows the engine's message.
export function describeFault(entry) {
  if (!entry) return null;
  const copy = COPY[entry.error_code];
  if (!copy) return { headline: "The run stopped.", body: entry.message || "", action: null };
  return copy(entry.provider || "The model provider", !entry.node || BEFORE_PLANNING.has(entry.node));
}
