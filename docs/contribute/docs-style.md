# Docs Style Guide

The seven rules every page of these docs follows, for anyone who writes or reviews documentation.

Each rule has a one-line reason, because a rule without one gets argued every time.

1. **Only what is built goes in the repository.** Docs describe shipped behaviour in the present tense: no design documents, waves, phases, PR plans, decision ids, "Built notes" or "Status: design" banners. *Why: a reader cannot tell a plan from a fact, and a plan that lags the code misleads.*
2. **One topic, one page, one section.** The README and the package READMEs link to the docs and never copy them. *Why: two copies drift, and the reader trusts the wrong one.*
3. **Every page opens with one sentence saying what it covers and who it is for.** *Why: a reader decides in a few seconds whether the page is theirs.*
4. **Code, tests and comments never cite doc sections.** Tests are named after what they test. *Why: a section number breaks silently when the doc is edited, and a comment should explain the code, not point away from it.*
5. **Diagrams are Mermaid with no forced colours on anything GitHub or the docs site renders.** *Why: the reader picks the theme, and a forced colour can be unreadable on a dark canvas. Carry emphasis in shape and layout.*
6. **Docs change in the same PR as the code.** *Why: a doc that lags is worse than no doc.*
7. **Moved pages keep their old URLs working, through `mkdocs-redirects`.** Add an entry to `redirect_maps` in `mkdocs.yml` for every page you move or rename. *Why: people bookmark, link and search for pages; a move should not break them.*
   A link to the docs site from outside `docs/` (a README, `SECURITY.md`, code) names a version, usually `https://nadeem4.github.io/nl2sql/latest/<page>/`: the site keeps one copy per release, and only its front page redirects. `packages/nl2sql/tests/unit/test_docs_site_links.py` checks it.

## Checking a change

```bash
DISABLE_MKDOCS_2_WARNING=true mkdocs build --strict
```

The build fails on a broken link or a missing page. The boundary test (`packages/nl2sql/tests/architecture/test_boundaries.py`) rejects plan ids in parentheses, alone or inside a sentence, and the names of planning documents in source, tests and docs.
