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
        Rule("PlannerAnswer", {"plan": {"a": 1}}, when="employees"),
        Rule("PlannerAnswer", {"plan": {"a": 2}}, when="factories"),
        Rule("plain", "retry please"),
    ]).start()
    try:
        tools = [{"type": "function", "function": {"name": "PlannerAnswer"}}]
        r = _post(server.base_url, {"messages": [{"content": "tables: factories"}], "tools": tools})
        assert json.loads(r["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]) == {"plan": {"a": 2}}
        r = _post(server.base_url, {"messages": [{"content": "hello"}]})
        assert r["choices"][0]["message"]["content"] == "retry please"
        assert [c["name"] for c in server.calls] == ["PlannerAnswer", "plain"]
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


def test_a_bare_plan_answers_the_planner_wrapped():
    # The planner's structured output is PlannerAnswer (K2): a rule that answers with a plan alone is
    # wrapped as {"plan": ...}; one that already has "plan", and any other name, are sent as given.
    plan = {"tables": [], "select_items": []}
    server = FakeLLMServer([Rule("PlannerAnswer", plan, when="bare"), Rule("PlannerAnswer", json.dumps(plan)),
                            Rule("Other", plan)]).start()
    try:
        def ask(name, text):
            tools = [{"type": "function", "function": {"name": name}}]
            r = _post(server.base_url, {"messages": [{"content": text}], "tools": tools})
            return json.loads(r["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"])

        assert ask("PlannerAnswer", "bare") == {"plan": plan}
        assert ask("PlannerAnswer", "as a string") == {"plan": plan}
        assert ask("Other", "x") == plan
    finally:
        server.stop()


def test_an_anthropic_call_records_the_key_from_either_header():
    # anthropic 1.x sends x-api-key, 2.x sends Authorization: Bearer; both must record.
    server = FakeLLMServer([Rule("plain", "hi")]).start()
    try:
        for header, value in (("x-api-key", "key-a"), ("Authorization", "Bearer key-b")):
            req = urllib.request.Request(
                server.anthropic_base_url + "/v1/messages",
                data=json.dumps({"messages": [{"role": "user", "content": "hello"}]}).encode(),
                headers={"Content-Type": "application/json", header: value}, method="POST")
            urllib.request.urlopen(req).read()
        assert [c["api_key"] for c in server.calls] == ["key-a", "key-b"]
    finally:
        server.stop()