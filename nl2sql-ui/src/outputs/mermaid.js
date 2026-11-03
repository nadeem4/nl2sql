// Mermaid has no colour of its own, so the page's theme decides: neutral on the light page, dark on the
// dark one. The library is loaded by Mermaid.jsx on the first diagram.
export const mermaidTheme = (dark) => (dark ? "dark" : "neutral");

// `strict` stops a diagram from carrying script or click handlers; the model wrote the text, the page does not trust it.
export const mermaidConfig = (dark) => ({ startOnLoad: false, securityLevel: "strict", theme: mermaidTheme(dark) });
