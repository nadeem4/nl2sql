// Run with `npm test`. Wave C's Mermaid carries no colour, so the page's theme decides: neutral on the light page,
// dark on the dark one. The library itself is loaded on the first diagram (Mermaid.jsx), never at start-up.
import { test } from "node:test";
import assert from "node:assert/strict";
import { mermaidConfig, mermaidTheme } from "./mermaid.js";

test("the theme follows the page", () => {
  assert.equal(mermaidTheme(false), "neutral");
  assert.equal(mermaidTheme(true), "dark");
});

test("the config renders on demand and refuses scripts and links in a diagram", () => {
  const config = mermaidConfig(true);
  assert.equal(config.theme, "dark");
  assert.equal(config.startOnLoad, false);
  assert.equal(config.securityLevel, "strict");
});
