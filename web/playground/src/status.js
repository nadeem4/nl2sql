// What the top bar's status pill says: which mode the page is in, in a few
// words, on the same row as the wordmark and the nav.
//
// The pill used to be a mode line of up to three sentences that pushed the nav
// down once a key was saved. The words are the short form; `detail` is the
// sentence the line used to carry, kept whole so nothing it said is lost. The
// page shows it as the pill's tooltip and reads it to a screen reader.
//
// `tone` is "ok" when a question can be answered live and "warn" when it
// cannot (no key yet, or replay answering only from recordings).

import { NO_KEY_REASON, hostedNote } from "./firstRun.js";
import { providerName } from "./settings.js";

// The replay sentence: it claims recorded answers only when the server loaded some.
export function replayNote(recorded, total, canSet) {
  const fix = canSet ? "add an API key under Settings or restart with --api-key" : "restart with --api-key";
  if (!recorded) {
    return `No API key found, and replay mode has no recorded answers, so no question can be answered. To ask questions, ${fix}.`;
  }
  return `No API key found. ${recorded} of ${total} guided questions answer from recorded model responses; for any other question, ${fix}.`;
}

function heldProviders(apiKeys) {
  if (!apiKeys || typeof apiKeys !== "object") {
    return (apiKeys || "").trim() ? ["openai"] : [];
  }
  return Object.keys(apiKeys).filter((provider) => (apiKeys[provider] || "").trim());
}

export function modeStatus(meta, apiKeys, { canSet = false } = {}) {
  if (!meta) return null;
  if (meta.hosted) {
    const held = heldProviders(apiKeys);
    if (!held.length) {
      return { tone: "warn", label: "Hosted demo · No key yet", detail: NO_KEY_REASON };
    }
    const whose = held.length === 1 ? `${providerName(held[0])} key` : `${held.length} keys`;
    return {
      tone: "ok",
      label: `Hosted demo · ${whose} in this tab`,
      detail: `${hostedNote(meta, apiKeys)} Settings.`,
    };
  }
  if (meta.mode === "replay") {
    const recorded = meta.recorded_questions || 0;
    const total = (meta.questions || []).length;
    return {
      tone: "warn",
      label: recorded ? `Replay mode · ${recorded} of ${total} recorded` : "Replay mode · No recordings",
      detail: replayNote(recorded, total, canSet),
    };
  }
  return { tone: "ok", label: "Live · configured model", detail: "Questions go to the configured model." };
}
