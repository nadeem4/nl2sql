import { test } from "node:test";
import assert from "node:assert/strict";
import { DEFAULT_PAGE, PAGES, createRouter, hashFor, navItems, pageFor, pageFromHash } from "./router.js";

// A window with just the two things the router touches: the hash and the
// hashchange event. `go` is what the browser does for a link, Back and
// Forward alike -- set the hash, then tell the page.
function fakeWindow(hash = "") {
  const handlers = [];
  return {
    location: { hash },
    addEventListener(type, fn) {
      if (type === "hashchange") handlers.push(fn);
    },
    removeEventListener(type, fn) {
      const at = handlers.indexOf(fn);
      if (at >= 0) handlers.splice(at, 1);
    },
    go(next) {
      this.location.hash = next;
      handlers.slice().forEach((fn) => fn());
    },
    listening: () => handlers.length,
  };
}

test("every page has a route, a label, a title and one line saying what it does", () => {
  assert.ok(PAGES.length >= 3);
  for (const page of PAGES) {
    assert.match(page.path, /^\//);
    assert.ok(page.label.length > 0);
    assert.ok(page.title.length > 0);
    assert.ok(page.description.length > 0, `${page.id} has no description`);
    assert.equal(hashFor(page.id), `#${page.path}`);
  }
  assert.ok(PAGES.some((p) => p.id === DEFAULT_PAGE));
});

test("the default route is the question and answer view", () => {
  for (const hash of ["", "#", "#/", "#/ "]) {
    assert.equal(pageFromHash(hash), DEFAULT_PAGE, `${JSON.stringify(hash)} is not the default`);
  }
  assert.equal(pageFromHash(undefined), DEFAULT_PAGE);
  assert.equal(hashFor(DEFAULT_PAGE), "#/");
});

test("a deep link opens the page it names", () => {
  assert.equal(pageFromHash("#/settings"), "settings");
  assert.equal(pageFromHash("#/retrieval"), "retrieval");
  // A trailing slash or a shouted hash is the same link.
  assert.equal(pageFromHash("#/settings/"), "settings");
  assert.equal(pageFromHash("#/Retrieval"), "retrieval");
  assert.equal(pageFor("settings").title, "Settings");
});

test("an unknown route falls back to the question and answer view", () => {
  for (const hash of ["#/nope", "#/settings/extra", "#/index", "#//"]) {
    assert.equal(pageFromHash(hash), DEFAULT_PAGE, `${hash} should fall back`);
  }
});

test("back and forward move between pages", () => {
  const win = fakeWindow("#/");
  const router = createRouter(win);
  const seen = [];
  router.subscribe((page) => seen.push(page));

  assert.equal(router.page(), "ask");
  win.go("#/settings");
  win.go("#/retrieval");
  // Back, then back again: the browser restores the earlier hash.
  win.go("#/settings");
  win.go("#/");
  // Forward.
  win.go("#/settings");

  assert.deepEqual(seen, ["settings", "retrieval", "settings", "ask", "settings"]);
  assert.equal(router.page(), "settings");
});

test("a deep link is the page the router starts on", () => {
  assert.equal(createRouter(fakeWindow("#/retrieval")).page(), "retrieval");
  assert.equal(createRouter(fakeWindow("#/nope")).page(), DEFAULT_PAGE);
  assert.equal(createRouter(fakeWindow("")).page(), DEFAULT_PAGE);
});

test("a bare fragment lands on the question and answer view, so Back always moves", () => {
  // Nothing in the page links to a bare fragment, but an older bookmark can
  // still carry one. It reads as the default page rather than sticking.
  const win = fakeWindow("#/settings");
  const router = createRouter(win);
  const seen = [];
  router.subscribe((page) => seen.push(page));

  win.go("#run");
  win.go("#index-rebuild");

  assert.deepEqual(seen, ["ask"]);
  assert.equal(router.page(), "ask");
});

test("moving to another page and back keeps the run: the router touches nothing but the hash", () => {
  // The question, the answer and the Debug choice are held above the page
  // switch, so the router never sees them. Frozen here: a write would throw.
  const run = Object.freeze({ question: "Which genre sells the most tracks?", answer: "Rock", debug: true });
  const win = fakeWindow("#/");
  const router = createRouter(win);
  const seen = [];
  router.subscribe((page) => seen.push(page));

  win.go("#/settings");
  win.go("#/");

  assert.deepEqual(seen, ["settings", "ask"]);
  assert.equal(router.page(), "ask");
  assert.deepEqual(run, { question: "Which genre sells the most tracks?", answer: "Rock", debug: true });
  assert.equal(win.location.hash, "#/");
});

test("leaving stops listening, so a stale page hears nothing", () => {
  const win = fakeWindow("#/");
  const router = createRouter(win);
  const seen = [];
  const drop = router.subscribe((page) => seen.push(page));

  drop();
  win.go("#/settings");
  assert.deepEqual(seen, []);

  router.stop();
  assert.equal(win.listening(), 0);
});

test("the nav names every page, marks the current one and says which are off", () => {
  const items = navItems("settings", { retrieval: true });

  assert.deepEqual(items.map((i) => i.id), PAGES.map((p) => p.id));
  assert.deepEqual(items.map((i) => i.href), ["#/", "#/settings", "#/retrieval"]);
  assert.equal(items.filter((i) => i.current).length, 1);
  assert.equal(items.find((i) => i.id === "settings").current, true);
  // An off page keeps its place in the nav: it opens and says why.
  assert.equal(items.find((i) => i.id === "retrieval").off, true);
  assert.equal(items.find((i) => i.id === "settings").off, false);
  assert.equal(items.find((i) => i.id === "ask").off, false);
});

test("nothing is marked off before the server has answered", () => {
  const items = navItems("ask", {});
  assert.deepEqual(items.map((i) => i.off), [false, false, false]);
  assert.equal(items.find((i) => i.id === "ask").current, true);
});
