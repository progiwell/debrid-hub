## Linked Issue

Fixes #<ISSUE_NUMBER>
<!-- For minor chores/hotfixes without an issue, replace with BYPASS-LINK-ISSUE -->

## Summary

- What changes are included in this PR?

## Test Plan

- [ ] `PYTHONPATH=server venv/bin/pytest server/tests/ -x -v`
- [ ] `npm run typecheck && npm run build` (if frontend/extension changed)

## PR Checklist

- [ ] **PR Size & Focus**: Single concern, ≤ 400–600 net changed source lines / ≤ 8–10 files (no compiled `dist/` bundles).
- [ ] **Tests**: Unit tests added or updated for new logic and edge cases.
- [ ] **Security**: No hardcoded secrets, API keys, or sensitive credentials.
- [ ] **Clean History**: Atomic commit(s) ready for squash merge.
