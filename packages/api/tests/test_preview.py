"""The link preview: tags in the served head, absolute URLs on the host the visitor typed (moved from the playground)."""
import re

import pytest
from starlette.requests import Request

from nl2sql_api import preview


def _request(headers=None, base="http://testserver"):
    scope = {"type": "http", "method": "GET", "path": "/", "query_string": b"", "scheme": "http",
             "server": ("testserver", 80), "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
             + [(b"host", b"testserver")]}
    return Request(scope)


def _tag(html, key):
    match = re.search(rf'<meta (?:name|property)="{re.escape(key)}" content="([^"]*)"', html)
    assert match, f"no {key} tag"
    return match.group(1)


def _page(**headers):
    return preview.with_preview("<html><head></head><body></body></html>", _request(headers))


def test_the_tags_are_in_the_head():
    html = _page()
    assert html.index("og:title") < html.index("</head>")
    assert _tag(html, "og:type") == "website"
    assert _tag(html, "twitter:card") == "summary_large_image"
    assert _tag(html, "description") == _tag(html, "og:description")
    assert html.count('name="description"') == 1


def test_urls_are_absolute_and_name_the_host_the_visitor_typed():
    html = _page(**{"x-forwarded-proto": "https", "x-forwarded-host": "other-demo.hf.space"})
    assert _tag(html, "og:url") == "https://other-demo.hf.space/"
    assert _tag(html, "og:image") == "https://other-demo.hf.space/social-card.png"


@pytest.mark.parametrize("host", ["nadeem4nk-nl2sql-demo.hf.space", "nl2sql.codewithnk.com"])
def test_the_public_space_names_its_custom_domain(host):
    html = _page(**{"x-forwarded-proto": "https", "x-forwarded-host": host})
    assert _tag(html, "og:url") == "https://nl2sql.codewithnk.com/"
    assert _tag(html, "twitter:image") == "https://nl2sql.codewithnk.com/social-card.png"


def test_without_a_proxy_the_urls_are_the_ones_the_app_was_reached_on():
    assert _tag(_page(), "og:url") == "http://testserver/"


@pytest.mark.parametrize("host", ['evil"><script>alert(1)</script>', "not a host", "", " "])
def test_a_forged_host_cannot_write_a_url_into_the_page(host):
    html = _page(**{"x-forwarded-host": host})
    assert "<script>alert(1)</script>" not in html
    assert _tag(html, "og:image").endswith("/social-card.png")

