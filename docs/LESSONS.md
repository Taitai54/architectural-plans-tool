# Lessons learned: architectural plans tool

A sense-check and reuse guide. It records what was built, what went wrong, what worked, and a playbook for doing something similar again (for example, turning another CAD/PDF drawing into a clean, measurable view).

Repo: <https://github.com/Taitai54/architectural-plans-tool>

---

## 1. What the tool is

| Item | Detail |
|---|---|
| Goal | Take an architectural PDF, strip the noise, show clear true lengths, drop in items (fridge etc.) at true size, review markups and design options, use an AI assistant |
| Shape | Local-only. FastAPI backend (127.0.0.1) plus one vanilla-JS file, `frontend/index.html` |
| Run (Windows) | Double-click [`run.bat`](../run.bat) (macOS/Linux: [`run.sh`](../run.sh)) |
| Tests | `pytest` in `backend/tests` (109 tests at time of writing) |
| AI providers | Claude, OpenAI, Gemini, Local (Ollama / any OpenAI-compatible), Demo |

---

## 2. Timeline of what was learned

| # | Problem | Cause | Fix | Lesson |
|---|---|---|---|---|
| 1 | "Unexpected token 'I', Internal S… is not valid JSON" | Server returned a plain-text 500; the viewer always called `.json()` | Global JSON error handler; viewer reads text first | Always return JSON errors, and make the client tolerate non-JSON |
| 2 | Changes kept going to a side branch | Work was not on `main` | PR then squash-merge to `main` only | Agree the delivery path early: branch → PR → merge to `main` |
| 3 | Furniture did not appear when dropped | HTML5 drag-and-drop was unreliable in the user's browser | Replaced with pointer events (`pointerDrag`: press, move, release) | Prefer pointer events over native DnD for anything the user must rely on |
| 4 | Double placement after the DnD change | Both the svg `pointerup` and the drop handler fired | `if (!drag) return` plus a mode guard | When replacing an interaction, grep for every handler that still reacts to the same event |
| 5 | Item "didn't appear" when no scale was set | No scale, so no true size to draw | Inline prompt (`needScale`), visible hint, focused ratio box | An error the user can miss is a bug; make the next step obvious |
| 6 | Scale had to be typed | Scale is in the PDF text | `assign_scales`: title block "1:200 @ A1", a label next to a drawing | Read what the document already says before asking the user |
| 7 | Wrong scale shared across drawings | One label applied to everything | A label must say "Scale" or name a paper size; match against the page size; per-drawing scales plus "apply to all" | Scale is per drawing, not per sheet |
| 8 | Hatch removal "removed all the walls" | Walls were drawn as hatch lines, not fills | Union and smooth the hatch into outlined **wall bands** in the backend | Understand how the CAD export draws a thing before filtering it |
| 9 | Clean view still noisy | Loose marks, leaders and X-markers around walls | Tidy: join strokes by shared vertices (structure) and proximity (hatch); keep large components; hide short off-axis strokes | Filter by connectivity, not by bounding-box overlap (overlap joined everything) |
| 10 | Glass run wrong in the extension | Rule matched worktop lines, then a slot drain | Bundle rule: ≥4 parallel full-length lines whose ends meet solid wall, none running alongside a wall | A single geometric rule overfits; require several independent conditions |
| 11 | "Still the old version" | Backend change needs a restart and a fresh upload; old server still held port 8000 (`Errno 10048`) | `run.bat` kills the stale listener; viewer warns when `extract_version` is old | Make staleness visible; never rely on the user to restart |
| 12 | Page switch lost edits | Save ran after the page changed | `pendingSave` captures payload and page at edit time; `loadPage` flushes first | Capture state at the moment of the edit, not at save time |
| 13 | Windows file errors | Atomic rename fails on Windows when the file is open | `write_json` retries `PermissionError`, unique tmp names, utf-8 | Test the file layer for Windows early |

---

## 3. Technical findings

### 3.1 Reading architectural PDFs

| Finding | Detail |
|---|---|
| Vector first | PyMuPDF `get_drawings` gives exact geometry. OCR is only for text that is not selectable |
| Classify every path | structure (thick), detail, hatch, fine, fills, text, dashed. Use **relative** stroke thresholds, because sheet sizes and exporters differ |
| Walls are often hatch | Dense parallel hairlines, not filled shapes. Union them with shapely: `buffer(-3.2).buffer(2.2).simplify(0.8)`, area ≥ 25, cap ≈ 4000 polygons |
| Glazing is a bundle | Several thin parallel lines, full length, ends on walls. Redraw in a distinct colour |
| Title block is a trap | It contains lines and text that look like content. Exclude it explicitly |
| Scale lives in text | "1:200 @ A1" in the title block, or a "Scale" label near a drawing. Cross-check with the page paper size and ignore page counts such as "Sheet 1/3" |
| True length | `mm = pt × mm_per_pt × scale_ratio`. Keep one function for this and test it |
| Large sheets are slow | Cap path counts (`MAX_DRAWINGS = 400_000`), use spatial grids for neighbours, cap polygon counts |

### 3.2 Viewer (single-file vanilla JS)

| Finding | Detail |
|---|---|
| SVG groups per category | `CAT_ORDER` controls z-order. Presets are just dictionaries of on/off flags |
| Keep a revert point | Branch `backup/before-structure-view` (commit `bf182d6`) was created before the big view rewrite |
| Interaction | Pointer events everywhere. Right and middle buttons pan only. Handle `pointercancel`. Ignore shortcuts while typing in inputs |
| Ruler | Reject drags under 0.5 pt so a click does not create a zero-length measurement |
| Save flow | `scheduleSave` snapshots; `flushSave` sends; `beforeunload` uses keepalive fetch; `loadSeq` drops stale loads |

### 3.3 Backend hardening (local does not mean safe)

| Control | Why |
|---|---|
| Bind to 127.0.0.1 | Not reachable from the network |
| `TrustedHostMiddleware` plus Origin check | Stops DNS-rebinding and drive-by requests from web pages |
| 5 MB state limit; NaN rejected in pydantic models | Prevents corrupt state files |
| Validation handler that does not echo input | Avoids leaking pasted content or NaN in errors |
| Per-job locks; Windows-safe `write_json` | Concurrent saves and Windows rename behaviour |
| `.env` git-ignored; keys never pasted in chat | Keys live in `backend/.env` or environment secrets |

### 3.4 AI providers

| Finding | Detail |
|---|---|
| Provider-agnostic interface | One class per provider, a shared `list_models`, and a datalist input so any model name can be typed |
| Local models | `LocalProvider` talks to any OpenAI-compatible endpoint. Default `http://localhost:11434/v1`. http(s) only, no proxy |
| `.env` parsing | Handle BOM, UTF-16 (PowerShell writes this) and inline comments |
| Not yet tested live | Claude / OpenAI / Gemini calls were not run from the cloud. Keys must come via environment settings. Demo and Local paths are covered by tests |

---

## 4. Process findings

| What worked | What did not |
|---|---|
| Headless Chromium (Playwright) to see the real result, then screenshots to compare | Judging a fix from code or unit tests alone |
| The user's screenshots plus the sample PDFs as the ground truth | Guessing at what the user's browser showed |
| Independent code reviews (frontend and backend, in parallel) | A single reviewer on one pass. Reviews found Windows and save-race bugs I had not |
| Small squash-merged PRs, each with one purpose | Large mixed changes, which made regressions hard to attribute |
| Keeping a revert branch before risky changes | Force-pushing a branch that had duplicated squashed content, which caused a merge conflict |
| Making staleness visible (version banner, port-kill) | Telling the user to "restart and re-upload" in prose |
| Tests that failed loudly on refactor mistakes (a dropped `drawings` variable) | Shell command substitution in `sed` against files containing `$(`; it corrupted a line. Use the Edit tool instead |

---

## 5. Playbook for a similar project

1. **Get samples first.** Ask for 2–3 real files covering different sheet types (plan, elevations, site plan) before designing anything.
2. **Inspect the vector data.** Dump path counts by stroke width, fill and dash. Decide categories from the numbers, not assumptions.
3. **Find out how each thing is drawn.** Walls: fill or hatch? Glass: how many lines? Do this on the user's own file.
4. **Build the pipeline backend-side**: extract, classify, derive higher-level shapes (wall bands, rooms), version the output (`extract_version`).
5. **Make the viewer a thin renderer** with presets. Put heuristics in one place per feature, each behind a layer switch so the user can turn it off.
6. **Test in a real browser headlessly** and attach screenshots to your reasoning. Compare before and after on the same file.
7. **Add one heuristic at a time**, with at least two independent conditions, and test it against a case it should *reject* (the slot drain).
8. **Ship small PRs to `main`**, with a revert branch before anything risky.
9. **Plan for stale state**: version stamps, a visible banner, a launcher that cleans up old processes.
10. **Target the user's platform.** Windows paths, ports, encodings and file locking all showed up here.
11. **Review with independent reviewers** after each large change, then verify their findings rather than accepting them.

---

## 6. Known gaps and ideas

| Area | Gap |
|---|---|
| Wall bands | Edges are slightly wavy. Some fine detail near the extension's top-left corner is smoothed away |
| Tidy | Slow on very large sheets |
| Rooms | Stress cases: a1/a3 areas, a2 thin walls, a7 extra region, h1 furniture. Labels can split a room; OCR tile-edge fragments; slow on large rooms |
| Old jobs | Plans uploaded before `extract_version = 2` keep the old look until re-uploaded |
| Lists | Pins capped at 500 (similar caps elsewhere) |
| Glazing | Only axis-aligned bundles are detected. Angled or curved glazing is not |
| AI | Live Claude / OpenAI / Gemini calls are untested from the cloud |

---

## 7. Quick reference

| Task | Command or place |
|---|---|
| Run the app (Windows) | [`run.bat`](../run.bat) |
| Run the tests | `cd backend && python -m pytest` |
| Viewer code | [`frontend/index.html`](../frontend/index.html) |
| Extraction and wall bands | [`backend/extract.py`](../backend/extract.py) |
| Scale reading | [`backend/autodetect.py`](../backend/autodetect.py), [`backend/ocr.py`](../backend/ocr.py) |
| AI providers | [`backend/ai.py`](../backend/ai.py) |
| API and hardening | [`backend/main.py`](../backend/main.py) |
| Setup and AI keys | [`README.md`](../README.md) |
