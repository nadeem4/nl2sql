// A fetch that records every call a client file makes: method, path under /api/v1 and the parsed body.
export function fakeApi(answer = {}) {
  const calls = [];
  globalThis.sessionStorage = { getItem: () => null, setItem() {}, removeItem() {} };
  globalThis.fetch = async (url, init = {}) => {
    calls.push({ method: init.method, path: String(url).replace("/api/v1", ""), body: init.body ? JSON.parse(init.body) : undefined });
    return new Response(JSON.stringify(answer), { status: 200 });
  };
  return calls;
}
