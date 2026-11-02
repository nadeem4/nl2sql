// Run with `npm test`. The chart series colours are read off the stylesheet: each of the four hues must stand out from
// the card it is drawn on (3:1, WCAG's bar for graphics) in both themes. The fifth series colour is the neutral ink
// token, which base.css holds to text contrast already.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = (name) => readFileSync(new URL(`../styles/${name}`, import.meta.url), "utf8");
const outputs = read("outputs.css");
const base = read("base.css");

// The `--name: #hex` declarations of the first block that opens with `opener`, in the stylesheet text.
function tokens(css, opener) {
  const from = css.indexOf(opener);
  assert.ok(from >= 0, `${opener} not found`);
  const body = css.slice(from, css.indexOf("}", from));
  return Object.fromEntries([...body.matchAll(/(--[\w-]+):\s*(#[0-9a-fA-F]{6})/g)].map((m) => [m[1], m[2]]));
}

const channel = (c) => {
  const v = c / 255;
  return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
};
const luminance = (hex) => {
  const [r, g, b] = [1, 3, 5].map((i) => channel(parseInt(hex.slice(i, i + 2), 16)));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};
const contrast = (a, b) => {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
};

const light = { series: tokens(outputs, ":root {"), card: tokens(base, ":root {")["--raised"] };
const dark = {
  series: tokens(outputs, "@media (prefers-color-scheme: dark) {\n  :root {"),
  card: tokens(base, "@media (prefers-color-scheme: dark) {\n  :root {")["--raised"],
};

for (const [mode, { series, card }] of Object.entries({ light, dark })) {
  test(`${mode}: the four series colours hold 3:1 on the card`, () => {
    for (const name of ["--series-1", "--series-2", "--series-3", "--series-4"]) {
      assert.ok(series[name], `${name} is not set for ${mode}`);
      const ratio = contrast(series[name], card);
      assert.ok(ratio >= 3, `${name} ${series[name]} is ${ratio.toFixed(2)}:1 on ${card}`);
    }
  });
}
