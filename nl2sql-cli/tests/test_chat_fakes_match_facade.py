"""The stand-in engine of the chat tests keeps the shape of the real facade, or a rename would pass here."""
from __future__ import annotations

import importlib
import inspect

import pytest
from chat_fakes import FakeConversations, FakeEngine

REAL = {
    "conversations": ("nl2sql.api.conversations_api", "ConversationsAPI"),
    "results": ("nl2sql.api.results_api", "ResultsAPI"),
    "approvals": ("nl2sql.approvals.api", "Approvals"),
    "knowledge": ("nl2sql.api.knowledge_api", "KnowledgeAPI"),
    "datasources": ("nl2sql.api.datasource_api", "DatasourcesAPI"),
}


def _real(namespace):
    module, cls = REAL[namespace]
    return getattr(importlib.import_module(module), cls)


def _check(fake, real):
    f = {n: q for n, q in inspect.signature(fake).parameters.items() if n != "self"}
    r = {n: q for n, q in inspect.signature(real).parameters.items() if n != "self"}
    unknown = [n for n in f if n not in r]
    missing = [n for n, q in r.items() if q.default is inspect.Parameter.empty
               and q.kind in (q.POSITIONAL_OR_KEYWORD, q.KEYWORD_ONLY) and n not in f]
    assert not unknown and not missing, f"the fake takes {unknown} the facade lacks, and lacks {missing} it requires"


@pytest.mark.parametrize("name", ["start", "get", "list", "stream", "tool", "retry", "report"])
def test_fake_conversations_match_the_facade(name):
    _check(getattr(FakeConversations([]), name), getattr(_real("conversations"), name))


@pytest.mark.parametrize("namespace,name", [("results", "page"), ("approvals", "grant"), ("approvals", "list"),
                                            ("knowledge", "save_choice"), ("datasources", "list")])
def test_fake_namespaces_match_the_facade(namespace, name):
    _check(getattr(getattr(FakeEngine([]), namespace), name), getattr(_real(namespace), name))
