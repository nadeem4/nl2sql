// The Connect flow in words: the steps, what a test and an add report, and which step the person is on.
// Pure, so `npm test` covers it.

export const STEPS = [
  { id: "connection", label: "Connection" },
  { id: "test", label: "Test" },
  { id: "add", label: "Add" },
  { id: "index", label: "Index" },
  { id: "profile", label: "Profile" },
  { id: "questions", label: "Starter questions" },
  { id: "review", label: "Review" },
];

// Each step with its state against `current`: earlier ones are done, later ones still to do.
export function stepStates(current) {
  const at = STEPS.findIndex((s) => s.id === current);
  return STEPS.map((step, i) => ({ ...step, state: i < at ? "done" : i === at ? "current" : "todo" }));
}

// The body of `POST /datasources/test` and `POST /datasources`: one entry of `datasources` in nl2sql.yaml.
export function buildConfig({ id, type, description, connection }) {
  const config = { id: (id || "").trim(), type };
  if ((description || "").trim()) config.description = description.trim();
  config.connection = connection;
  return config;
}

// The adapter's stages (DatasourceStatus.stage) as a person says them.
const STAGES = {
  config: "configuration",
  driver: "driver",
  network: "network",
  auth: "sign-in",
  database: "database",
  permission: "permissions",
};

// What `POST /datasources/test` answered: connected, or the failed stage with its reason and fix.
export function describeTest(status) {
  const warnings = [...(status.warnings || [])];
  if (status.status === "ready") return { ok: true, headline: "Connected", reason: "", fix: "", warnings };
  const stage = STAGES[status.stage];
  return {
    ok: false,
    headline: stage ? `Failed at ${stage}` : "The connection failed",
    reason: status.reason || "",
    fix: status.fix || "",
    warnings,
  };
}

// The server's 422 `details` (`{loc, msg}`) as messages by form field: "config.connection.pool.size" is "pool.size".
export function fieldErrors(details) {
  const out = {};
  for (const { loc, msg } of details || []) {
    const path = (loc || []).filter((part) => part !== "config" && part !== "connection").join(".");
    if (path && !(path in out)) out[path] = msg;
  }
  return out;
}

// What a failed `POST /datasources` means for the page. NOT_WRITABLE is the one failure that leaves the datasource
// registered (the config is read-only, as in the Docker image): it works until the server restarts (D212).
export function describeAdd(error) {
  if (error.code === "NOT_WRITABLE") {
    return {
      added: true,
      saved: false,
      message: "Added, but not saved: the config here is read-only, so it works until the server restarts.",
      fix: error.fix || "Add it to the mounted config to keep it.",
      fields: {},
    };
  }
  return { added: false, saved: false, message: error.message, fix: error.fix || "", fields: fieldErrors(error.details) };
}
