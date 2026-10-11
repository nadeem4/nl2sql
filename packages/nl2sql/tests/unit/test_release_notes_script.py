"""``scripts/release_notes.py``: the release-page gate and the release body.

Every release has a page at ``docs/releases/X.Y.Z.md``, written by the
release-notes skill. The gate refuses a release PR whose version has no page,
and the publish pipeline lifts the page's summary block into the GitHub release
body. Both decide from files alone, so both are tested here.
"""
import importlib.util
import pathlib

import pytest

yaml = pytest.importorskip("yaml")

ROOT = pathlib.Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "scripts" / "release_notes.py"
RELEASE = ROOT / ".github" / "workflows" / "release_please.yml"
PUBLISH = ROOT / ".github" / "workflows" / "publish_pypi.yaml"

PAGE = """# NL2SQL 9.9.9

<!-- summary:start -->

A release for testing: it fixes one thing.

<!-- summary:end -->

## Highlights

- Something.
"""


def _load_script():
    spec = importlib.util.spec_from_file_location("release_notes", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def notes():
    return _load_script()


def _repo(tmp_path, version="9.9.9", page=PAGE):
    (tmp_path / ".release-please-manifest.json").write_text(f'{{".": "{version}"}}\n')
    if page is not None:
        (tmp_path / "docs" / "releases").mkdir(parents=True)
        (tmp_path / "docs" / "releases" / f"{version}.md").write_text(page, encoding="utf-8")
    return tmp_path


# --- the summary block ---------------------------------------------------------

def test_the_summary_is_the_text_between_the_markers(notes):
    assert notes.extract_summary(PAGE) == "A release for testing: it fixes one thing."


def test_a_page_without_markers_has_no_summary(notes):
    assert notes.extract_summary("# NL2SQL 9.9.9\n\nJust text.\n") is None


def test_an_empty_summary_block_is_no_summary(notes):
    page = "<!-- summary:start -->\n\n<!-- summary:end -->\n"
    assert notes.extract_summary(page) is None


# --- the gate ------------------------------------------------------------------

def test_the_version_comes_from_the_release_please_manifest(notes, tmp_path):
    assert notes.manifest_version(_repo(tmp_path, "1.2.3")) == "1.2.3"


def test_the_gate_passes_when_the_page_exists_with_a_summary(notes, tmp_path):
    ok, message = notes.check(_repo(tmp_path), "9.9.9")
    assert ok
    assert "docs/releases/9.9.9.md" in message


def test_the_gate_fails_on_a_missing_page_and_says_to_run_the_skill(notes, tmp_path):
    ok, message = notes.check(_repo(tmp_path, page=None), "9.9.9")
    assert not ok
    assert "docs/releases/9.9.9.md" in message
    assert "release-notes skill" in message


def test_the_gate_fails_on_a_page_without_a_summary_block(notes, tmp_path):
    ok, message = notes.check(_repo(tmp_path, page="# NL2SQL 9.9.9\n\nNo markers.\n"), "9.9.9")
    assert not ok
    assert "summary" in message


def test_the_cli_check_exits_non_zero_without_a_page(notes, tmp_path, capsys):
    assert notes.main(["check", "--root", str(_repo(tmp_path, page=None))]) == 1
    assert "release-notes skill" in capsys.readouterr().out


def test_the_cli_check_exits_zero_with_a_page(notes, tmp_path):
    assert notes.main(["check", "--root", str(_repo(tmp_path))]) == 0


def test_the_cli_check_takes_an_explicit_version_over_the_manifest(notes, tmp_path):
    root = _repo(tmp_path)  # page for 9.9.9, manifest 9.9.9
    assert notes.main(["check", "--root", str(root), "--version", "1.0.0"]) == 1


# --- the release body ----------------------------------------------------------

def test_the_summary_goes_above_the_generated_changelog(notes):
    body = notes.release_body("The summary.", "## Bug Fixes\n\n* a fix")
    assert body.startswith("The summary.\n")
    assert body.endswith("## Bug Fixes\n\n* a fix")


def test_prepending_twice_leaves_one_summary(notes):
    once = notes.release_body("The summary.", "## Bug Fixes")
    assert notes.release_body("The summary.", once) == once


def test_the_cli_body_prints_the_new_body(notes, tmp_path, capsys):
    current = tmp_path / "body.md"
    current.write_text("## Bug Fixes\n", encoding="utf-8")
    assert notes.main(["body", "9.9.9", str(current), "--root", str(_repo(tmp_path))]) == 0
    out = capsys.readouterr().out
    assert out.startswith("A release for testing: it fixes one thing.")
    assert "## Bug Fixes" in out


def test_the_cli_body_warns_and_fails_soft_without_a_page(notes, tmp_path, capsys):
    current = tmp_path / "body.md"
    current.write_text("## Bug Fixes\n", encoding="utf-8")
    code = notes.main(["body", "9.9.9", str(current), "--root", str(_repo(tmp_path, page=None))])
    assert code == 3
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "::warning::" in captured.err


# --- wiring --------------------------------------------------------------------

def test_every_release_page_in_the_repo_has_a_summary(notes):
    pages = sorted((ROOT / "docs" / "releases").glob("[0-9]*.md"))
    assert pages, "docs/releases/ has no release pages"
    for page in pages:
        assert notes.extract_summary(page.read_text(encoding="utf-8")), page.name


def test_the_released_version_has_a_page(notes):
    ok, message = notes.check(ROOT, notes.manifest_version(ROOT))
    assert ok, message


def test_the_gate_runs_after_release_please_refreshes_its_pr():
    workflow = yaml.safe_load(RELEASE.read_text(encoding="utf-8"))
    gate = workflow["jobs"]["release-notes-gate"]

    assert gate["needs"] == "release-please"
    # A push that tagged a release has no open release PR left to gate.
    assert "releases_created != 'true'" in gate["if"]
    assert gate["permissions"]["statuses"] == "write"
    steps = str(gate["steps"])
    assert "release-please--branches--main" in steps
    assert "release_notes.py check" in steps


def test_the_publish_prepends_the_summary_and_never_fails_the_release():
    job = yaml.safe_load(PUBLISH.read_text(encoding="utf-8"))["jobs"]["release-notes"]

    assert job["continue-on-error"] is True
    assert job["permissions"]["contents"] == "write"
    steps = str(job["steps"])
    assert "release_notes.py body" in steps
    assert "gh release edit" in steps
