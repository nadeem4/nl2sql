// The two things a clarification offers: use a reading for this turn, or also remember it for the next asker.

export const choiceBody = (option, execute = true) => ({ choice: option.id, execute });

export function rememberBody(request, option, term) {
  const body = { choice: { request, option_id: option.id } };
  const t = (term || "").trim();
  return t ? { ...body, term: t } : body;
}
