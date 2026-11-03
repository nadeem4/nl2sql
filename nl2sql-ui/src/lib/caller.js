// Who the caller is, from the `caller` field of GET /meta. The server computes it with the code the routes use,
// so the page never guesses: not from a conversation's owner, not from a route answering 403.

// `{id, admin}` for a caller, or null when /meta has not arrived or names none. `id` is null for a hosted visitor,
// whose id is the session's secret and is never sent.
export function callerOf(meta) {
  const caller = meta && meta.caller;
  return caller ? { id: caller.user_id || null, admin: Boolean(caller.admin) } : null;
}

// Whether to offer what only an admin may do. Unknown offers it: the flag decides what to show, and the route's 403
// stays the server's final word.
export const mayAdminister = (caller) => caller === null || caller.admin;

// Whether the caller is known to be an admin. For pages that are only for admins: unknown offers nothing.
export const isAdmin = (caller) => Boolean(caller && caller.admin);
