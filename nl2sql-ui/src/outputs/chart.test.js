// Run with `npm test`. The chart module turns a ChartSpec (the golden files in api/fixtures, real responses of the API)
// into plain geometry: scales, one line per series, bars in the spec's order, formatted numbers, and the words for a
// truncated chart. Chart.jsx only draws what this returns.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { chartModel, formatFull, formatValue, niceTicks, truncatedNote } from "./chart.js";

const load = (name) => JSON.parse(readFileSync(new URL(`../api/fixtures/${name}.json`, import.meta.url), "utf8"));
const line = load("ChartLine");
const bar = load("ChartBar");
const big = load("ChartBigNumber");

test("niceTicks: round steps that cover the data", () => {
  assert.deepEqual(niceTicks(0, 1297), [0, 500, 1000, 1500]);
  assert.deepEqual(niceTicks(900, 1700), [800, 1000, 1200, 1400, 1600, 1800]);
  assert.deepEqual(niceTicks(5, 5), [0, 2, 4, 6]);
  assert.deepEqual(niceTicks(-3, 8), [-5, 0, 5, 10]);
});

test("formatValue: grouped digits for small numbers, a suffix from a million, two decimals at most", () => {
  assert.equal(formatValue(59), "59");
  assert.equal(formatValue(1297), "1,297");
  assert.equal(formatValue(12.3456), "12.35");
  assert.equal(formatValue(2_500_000), "2.5M");
  assert.equal(formatValue(-1_200_000_000), "-1.2B");
  assert.equal(formatValue(null), "No value");
  assert.equal(formatValue("n/a"), "n/a");
  assert.equal(formatFull(2_500_000), "2,500,000");
});

test("big number: the value formatted, titled by its encoding", () => {
  const m = chartModel(big, { width: 600 });
  assert.equal(m.kind, "big_number");
  assert.equal(m.text, "59");
  assert.equal(m.label, "N");
});

test("bar: one bar per row, in the spec's order, from a zero baseline", () => {
  const m = chartModel(bar, { width: 600 });
  assert.equal(m.kind, "bar");
  assert.deepEqual(m.groups.map((g) => g.label), ["Rock", "Latin", "Metal", "Jazz"]);
  const widths = m.groups.map((g) => g.bars[0].w);
  assert.ok(widths[0] > widths[1] && widths[1] > widths[2] && widths[2] > widths[3]);
  assert.ok(m.groups.every((g) => g.bars[0].x === m.zeroX));
  assert.ok(m.groups.every((g, i) => i === 0 || g.bars[0].y > m.groups[i - 1].bars[0].y));
  assert.equal(m.groups[0].bars[0].text, "1,297");
  assert.equal(m.ticks[0].x, m.zeroX);
  assert.ok(m.ticks.at(-1).x <= m.plot.x1);
  assert.equal(m.legend.length, 0);
});

test("bar: a negative value grows left of the baseline", () => {
  const spec = { ...bar, data: { ...bar.data, rows: [["a", 10], ["b", -5]] } };
  const m = chartModel(spec, { width: 600 });
  const [a, b] = m.groups.map((g) => g.bars[0]);
  assert.equal(a.x, m.zeroX);
  assert.equal(b.x + b.w, m.zeroX);
});

test("bar: several measures make a group of bars and a legend", () => {
  const spec = {
    ...bar, y: [bar.y[0], { field: "plays", kind: "number", title: "Plays" }],
    data: { ...bar.data, columns: ["genre", "tracks", "plays"], rows: [["Rock", 10, 30], ["Jazz", 5, 2]] },
  };
  const m = chartModel(spec, { width: 600 });
  assert.equal(m.groups[0].bars.length, 2);
  assert.deepEqual(m.legend.map((l) => l.name), ["Tracks", "Plays"]);
  assert.deepEqual(m.groups[0].bars.map((b) => b.slot), [1, 2]);
});

test("line: one line per series value, in the order they first appear, sharing the scales", () => {
  const m = chartModel(line, { width: 640, height: 280 });
  assert.equal(m.kind, "line");
  assert.deepEqual(m.lines.map((l) => l.name), ["East", "West"]);
  assert.deepEqual(m.lines.map((l) => l.slot), [1, 2]);
  assert.ok(m.lines.every((l) => l.points.length === 3));
  const east = m.lines[0].points;
  assert.ok(east[0].x < east[1].x && east[1].x < east[2].x);
  // 1500 (East, Feb) is higher on the page, so a smaller y, than 1200 (East, Jan).
  assert.ok(east[1].y < east[0].y);
  assert.ok(m.lines[0].d.startsWith("M") && m.lines[0].d.includes("L"));
  assert.deepEqual(m.legend.map((l) => l.name), ["East", "West"]);
  // The y axis covers every value, and every point sits inside the plot.
  const top = m.yTicks.at(-1).label;
  assert.ok(Number(top.replace(/,/g, "")) >= 1700);
  for (const p of m.lines.flatMap((l) => l.points)) {
    assert.ok(p.x >= m.plot.x0 && p.x <= m.plot.x1 && p.y >= m.plot.y0 && p.y <= m.plot.y1);
  }
});

test("line: dates are placed by time and labelled by month when they are months", () => {
  const m = chartModel(line, { width: 640, height: 280 });
  assert.deepEqual(m.xTicks.map((t) => t.label), ["Jan 2025", "Feb 2025", "Mar 2025"]);
});

test("line: with no series column, each measure is a line; a missing value breaks it", () => {
  const spec = {
    kind: "line", title: "t", x: { field: "d", kind: "temporal", title: "D" },
    y: [{ field: "a", kind: "number", title: "A" }, { field: "b", kind: "number", title: "B" }], series: null,
    data: { columns: ["d", "a", "b"], truncated: false, rows: [["2025-01-01", 1, 5], ["2025-01-02", null, 6], ["2025-01-03", 3, 7]] },
  };
  const m = chartModel(spec, { width: 640, height: 280 });
  assert.deepEqual(m.lines.map((l) => l.name), ["A", "B"]);
  assert.equal(m.lines[0].d.match(/M/g).length, 2);
  assert.equal(m.lines[1].d.match(/M/g).length, 1);
});

test("a chart that was cut says so in words; one that was not says nothing", () => {
  assert.equal(truncatedNote(line), null);
  assert.equal(truncatedNote(bar), "Only the first 4 rows are drawn. The result has more.");
  assert.equal(truncatedNote({ data: { truncated: true, rows: [[1]] } }), "Only the first 1 row is drawn. The result has more.");
});

test("series beyond the fourth share the neutral slot rather than a fifth hue", () => {
  const rows = ["a", "b", "c", "d", "e", "f"].flatMap((r) => [["2025-01-01", r, 1], ["2025-02-01", r, 2]]);
  const m = chartModel({ ...line, data: { ...line.data, rows } }, { width: 640 });
  assert.deepEqual(m.lines.map((l) => l.slot), [1, 2, 3, 4, 5, 5]);
});
