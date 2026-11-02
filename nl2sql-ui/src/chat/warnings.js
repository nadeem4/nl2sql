// The warnings worth showing under an answer: not the ones about an attempt the run replaced, each once, and a
// notice when the rows were cut.

export function visibleWarnings(result) {
  if (!result) return [];
  const seen = new Set();
  const out = [];
  for (const w of result.warnings || []) {
    if (w.detail && w.detail.recovered) continue;
    const key = `${w.code}|${w.message}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(w);
  }
  if (result.truncated) out.push({ code: "TRUNCATED", message: "Only the first rows are shown: the result hit its row limit." });
  return out;
}
