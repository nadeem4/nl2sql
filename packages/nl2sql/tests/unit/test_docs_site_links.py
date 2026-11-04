"""Links into the docs site name a version: the site keeps one copy per version, so a page sits under /latest/."""
import pathlib
import re
import subprocess

REPO = pathlib.Path(__file__).resolve().parents[4]
SITE_PAGE = re.compile(r"https://nadeem4\.github\.io/nl2sql/([^\s)\"'>`]+)")
VERSIONED = re.compile(r"(latest|dev|v\d+\.\d+\.\d+)/")


def test_every_link_to_a_docs_page_goes_through_a_version():
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    offenders = []
    for name in tracked:
        if not name.endswith((".md", ".py", ".toml", ".js", ".jsx")) or name.startswith((".claude/", "docs/releases/")):
            continue
        try:
            text = (REPO / name).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            for path in SITE_PAGE.findall(line):
                if not VERSIONED.match(path):
                    offenders.append(f"{name}:{number}: {path}")
    assert not offenders, f"link to https://nadeem4.github.io/nl2sql/latest/<page> instead: {offenders}"
