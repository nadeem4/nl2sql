// What the home page says. The words live here, apart from the markup, so a
// test can hold each claim against what the playground really does: the plan
// is the model's, the checks and the SQL are code, a guided question replays
// without a key only when the server has a recording for it.

export const REPO = "https://github.com/nadeem4/nl2sql";
export const DOCS = "https://nadeem4.github.io/nl2sql/";

// Clips are served by the playground itself (`/clips/<name>` in app.py), not
// inlined into the page, so the bundle stays the size it was.
export const CLIP_ROOT = "/clips/";

export const HERO = {
  eyebrow: "Questions in, rows out",
  title: "Ask your database a question. See the plan before it becomes SQL.",
  lede:
    "NL2SQL turns a plain question into a typed query plan, checks it against your schema, and only then "
    + "writes SQL for your database. Every step is visible.",
  ctas: [
    { id: "try", label: "Try a sample question", kind: "primary" },
    { id: "key", label: "Use your own key", kind: "secondary", href: "#/settings" },
    { id: "how", label: "See how it works", kind: "link", target: "how" },
  ],
};

// A question's path, as the run page draws it: round marks for a step a model
// decides, square for code. Check is the gate the run page draws as a bar.
export const STEPS = [
  { name: "Question", what: "Picks the database and splits it up", kind: "model" },
  { name: "Plan", what: "Tables, joins, filters. Never SQL", kind: "model" },
  { name: "Check", what: "Against the schema and your role", kind: "code", gate: true },
  { name: "SQL", what: "Written from the plan, by code", kind: "code" },
  { name: "Rows", what: "Run read-only, then answered", kind: "code" },
];

export const FEATURES = [
  {
    id: "ask",
    page: "Ask",
    href: "#/ask",
    heading: "Ask, and get rows back",
    what: "Pick a guided question or type your own. The answer comes first, with the plan, the checks, the SQL "
      + "and the rows under it.",
    points: [
      "Ask as admin, analyst or viewer, and watch a role be refused.",
      "Every run says what it cost, and the rows download as CSV.",
    ],
    caption: "Ask a guided question and read the run",
  },
  {
    id: "pipeline",
    page: "Pipeline",
    href: "#/pipeline",
    heading: "Watch the plan become SQL",
    what: "Every step a question passes through, in order. Five are decided by a model and the rest by code, "
      + "and after a run each one shows its model, tokens and time.",
    points: [
      "The SQL is rendered from the checked plan by code, never written by the model.",
      "Debug on Ask opens any step's exact prompt and raw reply.",
    ],
    caption: "Every step of a run, and which ones call a model",
  },
  {
    id: "retrieval",
    page: "Retrieval",
    href: "#/retrieval",
    heading: "See what it looked up",
    what: "Search the index the engine searches, with the same local embedding model, and see which tables and "
      + "columns it would hand the planner.",
    points: [
      "No model is called, so no key is needed.",
      "Change k or lambda and the search runs again.",
    ],
    caption: "Search the index and see what MMR picks",
  },
];

export const MORE = [
  {
    label: "Run it on your machine",
    what: "Your own databases, no limits, and a key that never leaves your computer.",
    code: ['pip install "nl2sql-engine[demo]"', "nl2sql demo"],
  },
  { label: "Read the docs", href: DOCS, what: "How the plan, the checks and the adapters fit together." },
  { label: "Source on GitHub", href: REPO, what: "The engine, the REST API and this playground." },
];

export function clipFor(id) {
  return {
    src: `${CLIP_ROOT}${id}.webm`,
    srcDark: `${CLIP_ROOT}${id}-dark.webm`,
    poster: `${CLIP_ROOT}${id}.jpg`,
    posterDark: `${CLIP_ROOT}${id}-dark.jpg`,
  };
}

function guided(meta) {
  if (!meta) return [];
  const groups = meta.question_groups || [];
  const fromGroups = groups.flatMap((group) => group.questions || []);
  return fromGroups.length ? fromGroups : meta.questions || [];
}

// The question "Try a sample question" asks: the first guided question with a
// recorded answer, so it works with no key, else the first guided question.
export function sampleQuestion(meta) {
  const all = guided(meta);
  if (!all.length) return null;
  const recorded = new Set((meta && meta.recorded) || []);
  return all.find((q) => recorded.has(q)) || all[0];
}

function replays(meta) {
  if (!meta) return false;
  if (meta.hosted) return ((meta.recorded || []).length) > 0;
  return meta.mode === "replay" && (meta.recorded_questions || 0) > 0;
}

// The strip under the hero. The third fact is the one that depends on the
// server: it only says a key is optional where something answers without one.
export function facts(meta) {
  const base = ["No sign-in", "Sample databases included"];
  if (!meta || replays(meta)) return [...base, "A key is only needed for your own questions"];
  if (meta.hosted) return [...base, "Bring your own API key; it stays in this tab"];
  if (meta.mode === "replay") return [...base, "Add an API key to ask a question"];
  return [...base, "Runs on your machine, with your key"];
}
