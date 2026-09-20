import json
import urllib.request

from nl2sql.testing.fake_llm import FakeLLMServer, Rule


def _post(url, body):
    req = urllib.request.Request(url + "/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def test_dispatches_on_tool_name_and_substring():
    server = FakeLLMServer([
        Rule("PlanModel", {"a": 1}, when="employees"),
        Rule("PlanModel", {"a": 2}, when="factories"),
        Rule("plain", "retry please"),
    ]).start()
    try:
        tools = [{"type": "function", "function": {"name": "PlanModel"}}]
        r = _post(server.base_url, {"messages": [{"content": "tables: factories"}], "tools": tools})
        assert json.loads(r["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]) == {"a": 2}
        r = _post(server.base_url, {"messages": [{"content": "hello"}]})
        assert r["choices"][0]["message"]["content"] == "retry please"
        assert [c["name"] for c in server.calls] == ["PlanModel", "plain"]
    finally:
        server.stop()
