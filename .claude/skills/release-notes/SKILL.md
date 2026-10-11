---
name: release-notes
description: Write the human-readable release page docs/releases/X.Y.Z.md for an NL2SQL release and open its PR. Use when a release-please PR is open, before releasing, when the release-notes status check fails, or to write or backfill a release page for a past version.
---

# Release notes

Every release has a page at `docs/releases/X.Y.Z.md`. The `release-notes`
status on the release-please PR stays red until that page is on `main`, and the
publish pipeline copies the page's summary block into the GitHub release body.

## Rules

- No Claude or Claude Code attribution anywhere: page, commit, PR title or body.
- Never read `.env` files or anything under `nl2sql-demo*/`.
- Never merge the release-please PR, and never merge your own PR.
- Mermaid, if any, uses no colours and no `init` line (the docs site picks the theme).
- State only what git, the PRs and `CHANGELOG.md` support. If unsure, leave it out.

## Procedure

1. **Target version.** Use the version argument if given (backfill). Otherwise
   read it from the open release PR:
   `gh pr view release-please--branches--main --json body,title` and its
   `.release-please-manifest.json` (`git show origin/release-please--branches--main:.release-please-manifest.json`).
   The previous version is the tag before it (`git tag --sort=v:refname`).
2. **Gather what changed.**
   - `git log --oneline vPREV..vX.Y.Z` (or `vPREV..origin/main` for an
     unreleased version).
   - `gh pr view <n> --json title,body` for each merged PR in that range.
   - The `X.Y.Z` section of `CHANGELOG.md` (or of the release PR's diff).
3. **Match the house style.** Read the last two pages in `docs/releases/` (by
   version) and match their tone, length and structure.
4. **Write `docs/releases/X.Y.Z.md`** from [`template.md`](template.md), keeping
   every section in order:
   - Title `# NL2SQL X.Y.Z`, then the summary block: one or two sentences on
     what the release is for, between `<!-- summary:start -->` and
     `<!-- summary:end -->`, with a blank line inside each marker.
   - **Highlights**: two to four plain-language, user-facing bullets.
   - **What's included**: Features / Fixes / Docs, one line each, each linking
     its PR. Drop an empty group. Leave out release chores and CI-only changes
     unless a user would notice them.
   - **Known limits**: what it does not do yet. Honest, short.
   - **Upgrading**: the pip command, then breaking changes or "none".
   - **What's next**: one line naming the next planned version only.
   - The full changelog compare link `vPREV...vX.Y.Z`.
5. **Index and nav.** Add one line to `docs/releases/index.md`, newest first,
   and the page to the `Releases` section of `mkdocs.yml` `nav`, newest first.
6. **Check.** `mkdocs build --strict` and
   `python scripts/release_notes.py check --version X.Y.Z` must both pass.
7. **Open the PR** from a branch off `origin/main`, titled
   `docs(release): notes for X.Y.Z`. Once it merges, the release-please PR
   refreshes and its `release-notes` status turns green; the owner then merges
   the release PR.
