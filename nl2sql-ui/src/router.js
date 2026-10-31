// Where you are in the playground, kept in the address bar.
//
// A route is a hash that starts with "#/": "#/" is the home page, "#/ask" the
// question and answer view, "#/pipeline", "#/settings" and "#/retrieval" pages
// of their own. So a reload lands where you were and a link can be shared,
// with no server route to add. A route nobody serves reads as the home page; a
// bare fragment left by an older link (#run, #question, a station) was a place
// on the Ask page when Ask was the default, so it still opens Ask. Back and
// Forward always land somewhere. Nothing in the page links to a bare fragment: the skip
// control and the index warning move focus instead, which keeps the hash a
// route and keeps the history honest.
//
// The router owns the hash and nothing else. The question, the answer and the
// Debug choice are held above the page switch, so moving between pages is a
// change of view, never a change of run.

function stub(id, path, label, description, param = null) {
  return { id, path, label, title: label, description, stub: true, param };
}

export const PAGES = [
  {
    id: "home",
    path: "/",
    label: "Home",
    title: "nl2sql playground",
    description:
      "Ask a database a question and see the typed plan, the checks and the SQL before any rows come back.",
  },
  {
    id: "ask",
    path: "/ask",
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
      "Add your own API keys, one per provider, and choose which model runs each step. Both stay in this browser tab: the hosted demo keeps no key of its own and saves nothing you set here.",
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

  // Wave D's pages. Each is a stub (pages/<Name>.jsx) until the row that owns
  // it fills it, and stays out of the nav until then. `param` is the one id a
  // route may carry: "optional" (#/chat and #/chat/{id}) or "required".
  stub("chat", "/chat", "Chat", "Ask in conversation: the model picks the steps, and every answer shows how it was made.", "optional"),
  stub("dashboards", "/dashboards", "Dashboards", "Whole dashboards in workspaces, each tile with its takeaway.", "optional"),
  stub("connect", "/connect", "Connect", "Add a datasource, test it, index it and file its starter questions."),
  stub("knowledge", "/knowledge", "Knowledge", "What the engine knows about each datasource, and the proposals waiting for review."),
  stub("approvals", "/approvals", "Approvals", "Questions waiting for someone to grant or deny them."),
  stub("evals", "/evals", "Evals", "Run a suite against the server and read the report."),
  stub("trace", "/trace", "Trace", "One run, node by node.", "required"),
];

export const DEFAULT_PAGE = "home";

// Where a bare fragment from an older link lands: those all named places on Ask.
const FRAGMENT_PAGE = "ask";

const byId = new Map(PAGES.map((page) => [page.id, page]));
const byPath = new Map(PAGES.map((page) => [page.path, page]));

export function pageFor(id) {
  return byId.get(id) || byId.get(DEFAULT_PAGE);
}

export function hashFor(id, param) {
  const page = pageFor(id);
  return page.param && param ? `#${page.path}/${encodeURIComponent(param)}` : `#${page.path}`;
}

// A route, as opposed to a bare fragment such as "#run".
function isRouteHash(hash) {
  return typeof hash === "string" && hash.trim().startsWith("#/");
}

const HOME = { page: DEFAULT_PAGE, param: null };

// The page a hash names and the id it carries: {page, param}. Only the page
// name is case-insensitive; an id is kept as written. A route that is not one
// of ours reads as the home page.
export function routeFromHash(hash) {
  if (!isRouteHash(hash)) {
    const bare = typeof hash === "string" ? hash.trim().replace(/^#/, "") : "";
    return { page: bare ? FRAGMENT_PAGE : DEFAULT_PAGE, param: null };
  }
  let path = hash.trim().slice(1);
  if (path.length > 1 && path.endsWith("/")) path = path.slice(0, -1);
  const exact = byPath.get(path.toLowerCase());
  if (exact && exact.param !== "required") return { page: exact.id, param: null };
  const [, head, id, ...more] = path.split("/");
  const owner = byPath.get(`/${(head || "").toLowerCase()}`);
  if (!owner || !owner.param || !id || more.length) return HOME;
  try {
    return { page: owner.id, param: decodeURIComponent(id) };
  } catch {
    return HOME;
  }
}

export function pageFromHash(hash) {
  return routeFromHash(hash).page;
}

// One row of the header nav. A page the server has turned off keeps its place
// and is marked; opening it is how you read the reason.
export function navItems(currentId, off = {}) {
  return PAGES.filter((page) => !page.stub).map((page) => ({
    id: page.id,
    label: page.label,
    href: hashFor(page.id),
    current: page.id === currentId,
    off: Boolean(off[page.id]),
  }));
}

export function createRouter(win) {
  let route = routeFromHash(win.location.hash);
  const listeners = new Set();

  const onHashChange = () => {
    const next = routeFromHash(win.location.hash);
    if (next.page === route.page && next.param === route.param) return;
    route = next;
    listeners.forEach((listener) => listener(route));
  };

  win.addEventListener("hashchange", onHashChange);

  return {
    page: () => route.page,
    route: () => route,
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

// Swaps the page inside a view transition, so the new page crossfades in with
// a small rise (styles/base.css, `::view-transition-new(page)`) while the top bar,
// which has a transition name of its own, stays put. Without the API, under
// reduced motion, or if the browser refuses, the swap is simply instant.
// `update` must change the DOM before it returns (App uses flushSync).
export function swapView(doc, update, { reduce = false } = {}) {
  if (reduce || !doc || typeof doc.startViewTransition !== "function") {
    update();
    return;
  }
  try {
    doc.startViewTransition(update);
  } catch {
    update();
  }
}

// One indicator for the whole nav: it moves to the current tab and is scaled
// to its width (a 1px element, transform-origin left), so the slide animates
// transform alone. `link` and `nav` are bounding rects.
export function indicatorTransform(link, nav) {
  if (!link || !nav) return null;
  return `translateX(${Math.round(link.left - nav.left)}px) scaleX(${Math.round(link.width)})`;
}
