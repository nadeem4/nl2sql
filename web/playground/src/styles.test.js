// Run with `npm test`. The stylesheet's rules for motion and touch, read off
// styles.css itself: what may move, when, and how big a touch target is.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const css = readFileSync(new URL("./styles.css", import.meta.url), "utf8");

// Every rule as { selector, body, at }: `at` is the at-rule preludes around it,
// outermost first. Enough of a parser for this one file: no strings with braces.
function rules(text) {
  const out = [];
  const stack = [];
  const src = text.replace(/\/\*[\s\S]*?\*\//g, "");
  let start = 0;
  for (let i = 0; i < src.length; i += 1) {
    const ch = src[i];
    if (ch === "{") {
      const prelude = src.slice(start, i).trim();
      if (prelude.startsWith("@")) {
        stack.push(prelude);
        start = i + 1;
      } else {
        const end = src.indexOf("}", i);
        out.push({ selector: prelude, body: src.slice(i + 1, end), at: [...stack] });
        i = end;
        start = end + 1;
      }
    } else if (ch === "}") {
      stack.pop();
      start = i + 1;
    } else if (ch === ";" && !stack.length) {
      start = i + 1;
    }
  }
  return out;
}

const ALL = rules(css);
const inside = (rule, needle) => rule.at.some((a) => a.replace(/\s+/g, " ").includes(needle));
const motionOk = (rule) => inside(rule, "prefers-reduced-motion: no-preference");
const decls = (body) =>
  body.split(";").map((d) => d.trim()).filter(Boolean).map((d) => {
    const at = d.indexOf(":");
    return [d.slice(0, at).trim(), d.slice(at + 1).trim()];
  });

test("the motion tokens are defined once, on :root", () => {
  const tokens = {
    "--ease-out": "cubic-bezier(.2,.7,.2,1)",
    "--ease-in-out": "cubic-bezier(.65,0,.35,1)",
    "--dur-press": "90ms",
    "--dur-state": "160ms",
    "--dur-page": "180ms",
    "--dur-enter": "320ms",
    "--dur-data": "420ms",
  };
  for (const [name, value] of Object.entries(tokens)) {
    const defs = ALL.flatMap((r) => decls(r.body).filter(([p]) => p === name).map(([, v]) => ({ r, v })));
    assert.equal(defs.length, 1, `${name} is defined ${defs.length} times`);
    assert.equal(defs[0].r.selector, ":root", `${name} lives on ${defs[0].r.selector}`);
    assert.equal(defs[0].r.at.length, 0, `${name} sits inside ${defs[0].r.at.join(" ")}`);
    assert.equal(defs[0].v.replace(/\s+/g, ""), value);
  }
});

test("every animation, and smooth scrolling, sits behind prefers-reduced-motion: no-preference", () => {
  for (const rule of ALL) {
    for (const [prop, value] of decls(rule.body)) {
      const moves = (prop === "animation" || prop === "animation-name") && value !== "none";
      const smooth = prop === "scroll-behavior" && value === "smooth";
      if (moves || smooth) assert.ok(motionOk(rule), `${rule.selector} { ${prop}: ${value} } runs under reduced motion`);
    }
  }
  const keyframes = ALL.filter((r) => inside(r, "@keyframes"));
  for (const frame of keyframes) assert.ok(motionOk(frame), `${frame.at.join(" ")} is outside no-preference`);
});

test("only transform and opacity animate", () => {
  for (const frame of ALL.filter((r) => inside(r, "@keyframes"))) {
    for (const [prop] of decls(frame.body)) {
      assert.ok(prop === "transform" || prop === "opacity", `${frame.at.join(" ")} animates ${prop}`);
    }
  }
  for (const rule of ALL) {
    for (const [prop, value] of decls(rule.body)) {
      if (prop !== "transition" && prop !== "transition-property") continue;
      assert.doesNotMatch(value, /\b(all|width|height|top|left|right|bottom|margin|padding|max-height)\b/,
        `${rule.selector} transitions ${value}`);
    }
  }
});

test("no hover lift, no glass, no blur", () => {
  for (const rule of ALL.filter((r) => r.selector.includes(":hover"))) {
    assert.doesNotMatch(rule.body, /transform|box-shadow/, `${rule.selector} lifts on hover`);
  }
  assert.doesNotMatch(css, /backdrop-filter|filter:\s*blur/);
});

test("every hover rule is inside (hover: hover)", () => {
  for (const rule of ALL.filter((r) => r.selector.includes(":hover"))) {
    assert.ok(inside(rule, "hover: hover"), `${rule.selector} applies on touch too`);
  }
});

test("a coarse pointer gets 44px targets on the nav, chips, table heads, buttons and checkboxes", () => {
  const coarse = ALL.filter((r) => inside(r, "pointer: coarse") && /min-height:\s*44px/.test(r.body));
  const selectors = coarse.flatMap((r) => r.selector.split(",").map((s) => s.trim()));
  for (const wanted of [".nav-link", ".chip", ".table-head", ".ask", ".first-run-go", ".index-rebuild",
    ".settings-save", ".rate-btn", ".inspect-close", ".fault-action", ".fault-again", ".check"]) {
    assert.ok(selectors.includes(wanted), `${wanted} has no 44px minimum on a coarse pointer`);
  }
  const html = ALL.find((r) => r.selector === "html" && /-webkit-tap-highlight-color:\s*transparent/.test(r.body));
  assert.ok(html, "the tap highlight is not turned off for the page");
});

test("a route change crossfades the page and keeps the top bar still", () => {
  const topbar = ALL.find((r) => r.selector === ".topbar" && /view-transition-name/.test(r.body));
  assert.ok(topbar, "the top bar has no view-transition-name");
  const page = ALL.find((r) => r.selector === ".page" && /view-transition-name/.test(r.body));
  assert.ok(page, "main has no view-transition-name");
  const enter = ALL.find((r) => r.selector.includes("::view-transition-new(page)") && motionOk(r));
  assert.ok(enter && motionOk(enter), "the new page has no entry animation behind no-preference");
});

test("the nav has one indicator that slides between tabs over 240ms", () => {
  const ind = ALL.find((r) => r.selector.endsWith(".nav-ind") && /transition/.test(r.body));
  assert.ok(ind && motionOk(ind), "no sliding .nav-ind behind no-preference");
  assert.match(ind.body, /transform 240ms/);
});

test("results arrive in order: bars grow 30ms apart, gate tiles settle", () => {
  const bars = ALL.find((r) => r.selector.includes(".bar i") && /animation-delay:\s*calc\(var\(--i/.test(r.body));
  assert.ok(bars && motionOk(bars), "row and ledger bars are not staggered by --i");
  assert.match(bars.body, /30ms/);
  const tiles = ALL.find((r) => r.selector.includes(".gate-tile") && /animation/.test(r.body));
  assert.ok(tiles && motionOk(tiles), "gate tiles do not settle");
  const marks = ALL.find((r) => r.selector.includes(".mark") && /animation/.test(r.body) && inside(r, "no-preference")
    && /data-outcome/.test(r.selector));
  assert.ok(marks, "the spine's marks do not fill in order");
});

test("one skeleton rule and a rail without a reserved gutter", () => {
  assert.equal(ALL.filter((r) => r.selector === ".sk" && !r.at.length).length, 1, ".sk is defined twice");
  // A stable gutter on the scrolling rail left Chrome painting over the first
  // number on a line (the table count, the first index stat).
  for (const rule of ALL.filter((r) => r.selector.split(",").some((s) => s.trim() === ".rail"))) {
    assert.doesNotMatch(rule.body, /scrollbar-gutter/, "the rail reserves a scrollbar gutter");
  }
});
