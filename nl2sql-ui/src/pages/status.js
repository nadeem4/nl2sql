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

import { NO_KEY_REASON, hostedNote, recordedQuestions } from "./firstRun.js";
import { guidedGroups } from "./questions.js";
import { providerName } from "../lib/settings.js";

// The replay sentence: it claims recorded answers only when the server loaded some.
export function replayNote(recorded, total, canSet) {
  const fix = canSet ? "add an API key under Settings or put one in the project's .env and restart"
    : "put a key in the project's .env and restart";
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

// `replaying`: every model step runs on recordings (pipeline.js `onReplay`, from GET /about).
export function modeStatus(meta, apiKeys, { canSet = false, replaying = false } = {}) {
  if (!meta) return null;
  if (meta.mode === "hosted") {
    const held = heldProviders(apiKeys);
    if (!held.length) {
      const recorded = recordedQuestions(meta).length;
      if (recorded) {
        return {
          tone: "warn",
          label: "Hosted demo · Recorded runs",
          detail: `No key in this tab: ${recorded} guided questions answer from recorded runs. ${NO_KEY_REASON}`,
        };
      }
      return { tone: "warn", label: "Hosted demo · No key yet", detail: NO_KEY_REASON };
    }
    const whose = held.length === 1 ? `${providerName(held[0])} key` : `${held.length} keys`;
    return {
      tone: "ok",
      label: `Hosted demo · ${whose} in this tab`,
      detail: `${hostedNote(meta, apiKeys)} Settings.`,
    };
  }
  if (replaying) {
    const recorded = recordedQuestions(meta).length;
    const total = guidedGroups(meta).reduce((n, group) => n + group.questions.length, 0);
    return {
      tone: "warn",
      label: recorded ? `Replay mode · ${recorded} of ${total} recorded` : "Replay mode · No recordings",
      detail: replayNote(recorded, total, canSet),
    };
  }
  return { tone: "ok", label: "Live · configured model", detail: "Questions go to the configured model." };
}
