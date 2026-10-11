"""Release pages: the gate a release PR must pass, and the GitHub release body.

Every release has a human-readable page at ``docs/releases/X.Y.Z.md``, written
by the release-notes skill (``.claude/skills/release-notes/SKILL.md``). Its
first paragraph sits between ``<!-- summary:start -->`` and
``<!-- summary:end -->``.

    python scripts/release_notes.py check [--version X.Y.Z] [--root DIR]
        Exit 1 unless the page for the version exists and has a summary block.
        The version defaults to the one in .release-please-manifest.json.

    python scripts/release_notes.py body X.Y.Z CURRENT_BODY_FILE [--root DIR]
        Print the release body with the page's summary above CURRENT_BODY_FILE.
        Exit 3 with a ::warning:: and print nothing when there is no summary.

Standard library only: it runs on a bare CI runner.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ".claude/skills/release-notes/SKILL.md"
_SUMMARY = re.compile(r"<!--\s*summary:start\s*-->(.*?)<!--\s*summary:end\s*-->", re.S)


def page_path(version: str) -> str:
    return f"docs/releases/{version}.md"


def manifest_version(root: Path) -> str:
    return json.loads((Path(root) / ".release-please-manifest.json").read_text(encoding="utf-8"))["."]


def extract_summary(text: str) -> str | None:
    match = _SUMMARY.search(text)
    summary = match.group(1).strip() if match else ""
    return summary or None


def _summary_for(root: Path, version: str) -> str | None:
    page = Path(root) / page_path(version)
    return extract_summary(page.read_text(encoding="utf-8")) if page.is_file() else None


def check(root: Path, version: str) -> tuple[bool, str]:
    path = page_path(version)
    if not (Path(root) / path).is_file():
        # The first 140 characters become the commit status description.
        return False, (
            f"{path} is missing: run the release-notes skill, then merge its PR to main "
            f"before the release PR ({SKILL})."
        )
    if _summary_for(root, version) is None:
        return False, (
            f"{path} has no summary block (<!-- summary:start --> ... <!-- summary:end -->). "
            "Rewrite it with the release-notes skill."
        )
    return True, f"{path} is present."


def release_body(summary: str, body: str) -> str:
    if body.startswith(summary):
        return body
    return f"{summary}\n\n{body}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    gate = sub.add_parser("check")
    gate.add_argument("--version")
    gate.add_argument("--root", default=str(ROOT))
    body = sub.add_parser("body")
    body.add_argument("version")
    body.add_argument("current_body")
    body.add_argument("--root", default=str(ROOT))
    args = parser.parse_args(argv)

    root = Path(args.root)
    if args.command == "check":
        ok, message = check(root, args.version or manifest_version(root))
        print(message)
        return 0 if ok else 1

    summary = _summary_for(root, args.version)
    if summary is None:
        print(
            f"::warning::No summary in {page_path(args.version)}; the release body is left as generated.",
            file=sys.stderr,
        )
        return 3
    current = Path(args.current_body).read_text(encoding="utf-8")
    sys.stdout.write(release_body(summary, current))
    return 0


if __name__ == "__main__":
    sys.exit(main())
