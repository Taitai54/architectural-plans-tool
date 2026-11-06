# Handoff: Shapes and wrapping text notes

**Date**: 2026-11-06 10:36
**Module**: 09-validation

## Session Summary

Added to the architectural plans viewer (FastAPI backend, single-file vanilla JS/SVG frontend in `frontend/index.html`):
- Square and Circle shapes in the palette. They can be dragged on, resized and given text.
- Text size (`tsize`, mm, 0 = automatic) with wrapping to the box.
- Double-click editing of text inside shapes and furniture blocks, with A-, A+ and Auto.
- Free "T Text" markup notes now wrap (new `Mark.w` width in PDF points), have right-edge and corner handles, A-/A+/No wrap buttons, and double-click editing.

## Validation State

### Tests
- Status: 111 passed, 1 failed (`tests/test_api.py::test_rejects_corrupt_pdf`)
- The failure was already there before any change and did not change.
- Issues: none new.

### Visual Verification
- Screenshots captured: 1 (wrapped red note with selection box and handles)
- Method: Playwright MCP against uvicorn on port 8010, with scripted pointer and dblclick events. No real mouse drags.

## Current State

### Completed
- [x] Square/Circle shapes, palette group, resize, text size and wrap (commit 28ac7b4)
- [x] Double-click editor for shapes and blocks (28ac7b4)
- [x] Free text notes: wrap, handles, size buttons, double-click edit, undo, save of `w` (commit aec5f20 "updates")
- [x] Local main merged with origin (407d53c); `git status` clean, `main...origin/main` in sync

### In Progress
- [ ] None

### Pending
- [ ] Check export (PDF/PNG/SVG) shows wrapped notes
- [ ] Reopen a saved plan containing a mark with `w`
- [ ] Try real mouse drags on the handles (feel, hit area)
- [ ] Check that emptying a note and closing the editor removes it (coded, not tested)

## Files Modified

- `backend/main.py` (`Item.tsize`, `Mark.w`)
- `frontend/index.html` (shapes, `layoutText`/`autoFit`, `markLines`, `textBox`, `markHandles`, item/mark editor)

## Uncommitted Changes

None (verified with `git status -s`). HEAD is 407d53c. The `aec5f20` commit message "updates" is vague and was not written by this session.

## Next Steps

1. Manually test the pending items above in the browser.
2. Fix `test_rejects_corrupt_pdf` if it matters.
3. If needed, reword the `aec5f20` message (only with the user's OK, since it has been merged).

## Notes

- No `validation/validation-log.md` or `docs/retrospectives/` exist in this repo.
- Mark undo is a JSON snapshot stack, so mark objects are replaced on undo. Re-read `st.marks[...]` after undo.
- Patch scripts go in a file, not a Bash heredoc (the heredoc failed to parse earlier).
