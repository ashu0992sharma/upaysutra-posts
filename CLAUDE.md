# UpaySutra social posts

Project decisions, rules and open items live in the shared context file, which the owner also
uses in Claude Cowork and a claude.ai Project:

@docs/PROJECT_CONTEXT.md

When a change here settles or changes a decision, update that file in the same commit: add a
dated line to its Decision log and fix the section it affects. Close or add Open items as they change.

- Tests: `.venv/bin/python -m pytest -q`
- CLI: `.venv/bin/python -m postgen <command>` (plan, captions, render, reel, build-week, publish, preview, pause, sync)
