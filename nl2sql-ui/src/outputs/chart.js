// A ChartSpec as geometry: scales, ticks, one line per series, bars in the spec's order, formatted numbers. The
// engine chose the kind and the encodings; this module only places them. No React and no colour here:
// Chart.jsx draws what `chartModel` returns, and the series colours are CSS tokens (styles/outputs.css).

// Four series colours, then one neutral for every series after them (the palette's "other", styles/outputs.css).
const slotOf = (i) => Math.min(i, 4) + 1;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const asNumber = (v) => {
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  if (typeof v === "string" && v.trim() !== "" && Number.isFinite(Number(v))) return Number(v);
  return null;
};

// 1,297 · 12.35 · 2.5M · 1.2B. Small numbers keep every digit up to two decimals; from a million a suffix.
export function formatValue(value) {
  if (value === null || value === undefined) return "No value";
  const n = asNumber(value);
  if (n === null) return String(value);
  const abs = Math.abs(n);
  for (const [limit, suffix] of [[1e9, "B"], [1e6, "M"]]) {
    if (abs >= limit) return `${(n / limit).toFixed(1).replace(/\.0$/, "")}${suffix}`;
  }
  return n.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

// The number in full, for a tooltip or a screen reader.
export function formatFull(value) {
  const n = asNumber(value);
  return n === null ? formatValue(value) : n.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

// Round tick values that cover [min, max], about four steps of 1, 2 or 5 times a power of ten.
export function niceTicks(min, max, count = 4) {
  let lo = min;
  let hi = max;
  if (lo === hi) {
    lo = Math.min(lo, 0);
    hi = Math.max(hi, 0);
    if (lo === hi) hi = 1;
  }
  const raw = (hi - lo) / count;
  const power = 10 ** Math.floor(Math.log10(raw));
  const unit = raw / power;
  const step = (unit <= 1 ? 1 : unit <= 2 ? 2 : unit <= 5 ? 5 : 10) * power;
  const ticks = [];
  const end = Math.ceil(hi / step) * step + step / 1e6;
  for (let v = Math.floor(lo / step) * step; v <= end && ticks.length < 50; v += step) {
    ticks.push(Number(v.toFixed(10)));
  }
  return ticks;
}

export function truncatedNote(spec) {
  if (!spec.data || !spec.data.truncated) return null;
  const n = spec.data.rows.length;
  return `Only the first ${n} ${n === 1 ? "row is" : "rows are"} drawn. The result has more.`;
}

const column = (spec, encoding) => spec.data.columns.indexOf(encoding.field);
const linear = (d0, d1, r0, r1) => (v) => (d1 === d0 ? (r0 + r1) / 2 : r0 + ((v - d0) / (d1 - d0)) * (r1 - r0));
const clip = (text, n) => (text.length > n ? `${text.slice(0, n - 1)}…` : text);

function bigNumber(spec) {
  const value = spec.value || spec.y[0];
  const at = column(spec, value);
  const raw = spec.data.rows.length ? spec.data.rows[0][at] : null;
  return { kind: "big_number", label: value.title, text: formatValue(raw), full: formatFull(raw) };
}

// Horizontal bars: a long category name reads better beside its bar than under it. A group per row, one bar per
// measure, every bar from the zero baseline.
function bars(spec, { width }) {
  const BAR = 14;
  const GAP = 2;
  const measures = spec.y;
  const cats = spec.data.rows.map((row) => {
    const raw = row[column(spec, spec.x)];
    return raw === null || raw === undefined ? "(none)" : String(raw);
  });
  const left = Math.min(168, Math.max(...cats.map((c) => clip(c, 24).length), 4) * 7 + 16);
  const plot = { x0: left, x1: width - 64, y0: 8 };
  const values = spec.data.rows.map((row) => measures.map((m) => asNumber(row[column(spec, m)])));
  const flat = values.flat().filter((v) => v !== null);
  const ticksAt = niceTicks(Math.min(0, ...flat), Math.max(0, ...flat));
  const scale = linear(ticksAt[0], ticksAt.at(-1), plot.x0, plot.x1);
  const zeroX = scale(0);
  const groupH = measures.length * BAR + (measures.length - 1) * GAP;
  const step = groupH + 14;
  const groups = cats.map((label, i) => ({
    label: clip(label, 24),
    full: label,
    bars: measures.map((m, j) => {
      const v = values[i][j];
      const x = v === null ? zeroX : scale(v);
      return {
        name: m.title, slot: slotOf(j), value: v, text: formatValue(v), full: formatFull(v),
        x: Math.min(x, zeroX), w: Math.abs(x - zeroX), y: plot.y0 + i * step + j * (BAR + GAP), h: BAR,
      };
    }),
  }));
  plot.y1 = plot.y0 + cats.length * step;
  return {
    kind: "bar", width, height: plot.y1 + 28, plot, zeroX, groups,
    ticks: ticksAt.map((t) => ({ x: scale(t), label: formatValue(t) })),
    legend: measures.length > 1 ? measures.map((m, j) => ({ name: m.title, slot: slotOf(j) })) : [],
  };
}

// "Jan 2025" for months, "Mar 5" for days, a year for a long run of years.
function dateLabeller(times) {
  const days = times.map((t) => new Date(t));
  const span = (Math.max(...times) - Math.min(...times)) / 86_400_000;
  const allFirst = days.every((d) => d.getUTCDate() === 1);
  if (allFirst && span > 700 && days.every((d) => d.getUTCMonth() === 0)) return (t) => String(new Date(t).getUTCFullYear());
  if (allFirst) return (t) => `${MONTHS[new Date(t).getUTCMonth()]} ${new Date(t).getUTCFullYear()}`;
  return (t) => {
    const d = new Date(t);
    return `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}${span > 365 ? `, ${d.getUTCFullYear()}` : ""}`;
  };
}

function lines(spec, { width, height = 260 }) {
  const plot = { x0: 56, x1: width - 16, y0: 12, y1: height - 28 };
  const at = column(spec, spec.x);
  const rawX = spec.data.rows.map((r) => r[at]);
  const times = rawX.map((v) => (typeof v === "string" ? Date.parse(v) : NaN));
  const temporal = spec.x.kind === "temporal" && times.length > 0 && times.every((t) => !Number.isNaN(t));
  // A non-date x (a quarter label, a week number) is placed by its order of appearance.
  const distinct = [...new Set(rawX.map(String))];
  const position = rawX.map((v, i) => (temporal ? times[i] : distinct.indexOf(String(v))));
  const pmin = Math.min(...position);
  const pmax = Math.max(...position);
  const sx = linear(pmin, pmax, plot.x0 + 8, plot.x1 - 8);
  const labelFor = temporal ? dateLabeller(times) : null;
  const xLabel = (i) => (temporal ? labelFor(times[i]) : String(rawX[i]));

  // One line per series value, or one per measure when there is no series column.
  const series = [];
  const slot = new Map();
  const add = (name, i, y) => {
    if (!slot.has(name)) {
      slot.set(name, { name, slot: slotOf(series.length), raw: [] });
      series.push(slot.get(name));
    }
    slot.get(name).raw.push({ i, v: asNumber(y) });
  };
  if (spec.series) {
    const sa = column(spec, spec.series);
    const ya = column(spec, spec.y[0]);
    spec.data.rows.forEach((row, i) => add(String(row[sa]), i, row[ya]));
  } else {
    spec.y.forEach((m) => spec.data.rows.forEach((row, i) => add(m.title, i, row[column(spec, m)])));
  }

  const all = series.flatMap((s) => s.raw.map((p) => p.v)).filter((v) => v !== null);
  const ticksAt = niceTicks(Math.min(...all), Math.max(...all));
  const sy = linear(ticksAt[0], ticksAt.at(-1), plot.y1, plot.y0);

  const built = series.map((s) => {
    const raw = [...s.raw].sort((a, b) => position[a.i] - position[b.i]);
    let d = "";
    let pen = false;
    const points = [];
    for (const p of raw) {
      if (p.v === null) {
        pen = false;
        continue;
      }
      const x = sx(position[p.i]);
      const y = sy(p.v);
      d += `${pen ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`;
      pen = true;
      points.push({ x, y, label: xLabel(p.i), value: p.v, text: formatValue(p.v), full: formatFull(p.v) });
    }
    return { name: s.name, slot: s.slot, d, points };
  });

  // At most six labels along the x axis, from the distinct positions, in order.
  const order = [...new Set(position)].sort((a, b) => a - b).map((p) => position.indexOf(p));
  const every = Math.ceil(order.length / 6);
  const xTicks = order.filter((_, k) => k % every === 0).map((i) => ({ x: sx(position[i]), label: xLabel(i) }));
  return {
    kind: "line", width, height, plot, lines: built, xTicks,
    yTicks: ticksAt.map((t) => ({ y: sy(t), label: formatValue(t) })),
    legend: built.length > 1 ? built.map((l) => ({ name: l.name, slot: l.slot })) : [],
  };
}

// The geometry for a `line`, `bar` or `big_number` spec at the given width. A `table` spec has none: it is drawn as
// the rows table.
export function chartModel(spec, { width = 640, height } = {}) {
  if (spec.kind === "big_number") return bigNumber(spec);
  if (spec.kind === "bar") return bars(spec, { width });
  if (spec.kind === "line") return lines(spec, { width, height });
  return null;
}
