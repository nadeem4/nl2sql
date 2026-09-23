// Where you are in the playground, kept in the address bar.
//
// A route is a hash that starts with "#/": "#/" is the question and answer
// view, "#/pipeline", "#/settings" and "#/retrieval" are pages of their own. So a reload
// lands where you were and a link can be shared, with no server route to add.
// Anything else -- a route nobody serves, or a bare fragment left by an older
// link -- reads as the question and answer view, so Back and Forward always
// land somewhere. Nothing in the page links to a bare fragment: the skip
// control and the index warning move focus instead, which keeps the hash a
// route and keeps the history honest.
//
// The router owns the hash and nothing else. The question, the answer and the
// Debug choice are held above the page switch, so moving between pages is a
// change of view, never a change of run.

export const PAGES = [
  {
    id: "ask",
    path: "/",
    label: "Ask",
    title: "Ask",
    description:
      "Put a question to the demo database and follow the run: the plan, the checks, the SQL, the rows and what it cost.",
  },
  {
    id: "pipeline",
    path: "/pipeline",
    label: "Pipeline",
    title: "What runs a question",
    description:
      "Every step a question passes through, in order: the five a model decides, and the deterministic code that writes and checks the SQL around them.",
  },
  {
    id: "settings",
    path: "/settings",
    label: "Settings",
    title: "Settings",
    description:
      "Save an API key for the demo project and choose which model runs each step of the pipeline.",
    // The hosted demo saves nothing, so the page is only about the visitor's
    // own key; the same route, a different promise.
    hostedDescription:
      "Add your own API key for this browser tab. The hosted demo keeps no key of its own and stores nothing you paste here.",
  },
  {
    id: "retrieval",
    path: "/retrieval",
    label: "Retrieval",
    title: "Retrieval inspector",
    description:
      "Embed any text with the local model and run the engine's search against the live index: the nearest entries with their similarity, then the ones MMR picks.",
    note:
      "MMR trades similarity against overlap with earlier picks. There is no re-ranking model, and nothing on this page calls the LLM.",
  },
];

export const DEFAULT_PAGE = "ask";

const byId = new Map(PAGES.map((page) => [page.id, page]));
const byPath = new Map(PAGES.map((page) => [page.path, page]));

export function pageFor(id) {
  return byId.get(id) || byId.get(DEFAULT_PAGE);
}

export function hashFor(id) {
  return `#${pageFor(id).path}`;
}

// A route, as opposed to a bare fragment such as "#run".
function isRouteHash(hash) {
  return typeof hash === "string" && hash.trim().startsWith("#/");
}

export function pageFromHash(hash) {
  if (!isRouteHash(hash)) return DEFAULT_PAGE;
  let path = hash.trim().slice(1).toLowerCase();
  if (path.length > 1 && path.endsWith("/")) path = path.slice(0, -1);
  const page = byPath.get(path);
  return page ? page.id : DEFAULT_PAGE;
}

// One row of the header nav. A page the server has turned off keeps its place
// and is marked; opening it is how you read the reason.
export function navItems(currentId, off = {}) {
  return PAGES.map((page) => ({
    id: page.id,
    label: page.label,
    href: hashFor(page.id),
    current: page.id === currentId,
    off: Boolean(off[page.id]),
  }));
}

export function createRouter(win) {
  let page = pageFromHash(win.location.hash);
  const listeners = new Set();

  const onHashChange = () => {
    const next = pageFromHash(win.location.hash);
    if (next === page) return;
    page = next;
    listeners.forEach((listener) => listener(page));
  };

  win.addEventListener("hashchange", onHashChange);

  return {
    page: () => page,
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    stop() {
      win.removeEventListener("hashchange", onHashChange);
      listeners.clear();
    },
  };
}
