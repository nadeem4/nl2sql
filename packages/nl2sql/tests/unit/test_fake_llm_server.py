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


def test_usage_defaults_to_one_prompt_and_one_completion_token():
    server = FakeLLMServer([Rule("plain", "hi")]).start()
    try:
        r = _post(server.base_url, {"messages": [{"content": "hello"}]})
        assert r["usage"] == {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
    finally:
        server.stop()


def test_usage_is_configurable_per_rule_including_cached_and_reasoning_tokens():
    usage = {"prompt_tokens": 11000, "completion_tokens": 300, "total_tokens": 11300,
             "prompt_tokens_details": {"cached_tokens": 7680},
             "completion_tokens_details": {"reasoning_tokens": 128}}
    server = FakeLLMServer([Rule("plain", "hi", usage=usage)]).start()
    try:
        r = _post(server.base_url, {"messages": [{"content": "hello"}]})
        assert r["usage"] == usage
    finally:
        server.stop()


def test_langchain_openai_maps_cached_and_reasoning_tokens_into_usage_metadata():
    """Verifies the pinned langchain-openai, not an assumption about it."""
    from langchain_openai import ChatOpenAI

    usage = {"prompt_tokens": 11000, "completion_tokens": 300, "total_tokens": 11300,
             "prompt_tokens_details": {"cached_tokens": 7680},
             "completion_tokens_details": {"reasoning_tokens": 128}}
    server = FakeLLMServer([Rule("plain", "hi", usage=usage)]).start()
    try:
        llm = ChatOpenAI(model="gpt-4o", base_url=server.base_url, api_key="sk-fake", max_retries=0)
        meta = llm.invoke("hello").usage_metadata
    finally:
        server.stop()
    assert meta["input_tokens"] == 11000 and meta["output_tokens"] == 300 and meta["total_tokens"] == 11300
    assert meta["input_token_details"]["cache_read"] == 7680
    assert meta["output_token_details"]["reasoning"] == 128
