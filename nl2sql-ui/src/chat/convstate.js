// Which conversation a result belongs to. The page can show another conversation by the time a call ends, so a late
// result is applied only to the one it was for.

// The turn goes to the conversation on screen only when that is the one it was sent for.
export function withTurn(current, id, turn) {
  if (!current || current.id !== id) return current;
  if (current.turns.some((t) => t.index === turn.index)) return current;
  return { ...current, turns: [...current.turns, turn] };
}

// A conversation read again replaces the one on screen only when they are the same conversation.
export const withConversation = (current, next) => (current && current.id === next.id ? next : current);

// The address names `param`; `created` is the conversation this page just started, already on screen. That address is
// not loaded over the stream, and it is forgotten once seen, so opening the conversation again later loads it.
export const addressChange = (param, created) => ({ load: Boolean(param) && param !== created, created: null });
