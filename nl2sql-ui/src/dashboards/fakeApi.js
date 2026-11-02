// A fetch that records every call a client file makes: method, path under /api/v1 and the parsed body.
// `answer` is the JSON body for every call, or a function (method, path, body) giving one. A function may return
// {__status, code, message} to answer an error envelope with that status.
export function fakeApi(answer = {}) {
  const calls = [];
  globalThis.sessionStorage = { getItem: () => null, setItem() {}, removeItem() {} };
  globalThis.fetch = async (url, init = {}) => {
    const call = { method: init.method, path: String(url).replace("/api/v1", ""), body: init.body ? JSON.parse(init.body) : undefined };
    calls.push(call);
    const out = typeof answer === "function" ? answer(call.method, call.path, call.body) : answer;
    if (out && out.__status) {
      return new Response(JSON.stringify({ error: { code: out.code, message: out.message } }), { status: out.__status });
    }
    return new Response(JSON.stringify(out), { status: 200 });
  };
  return calls;
}
