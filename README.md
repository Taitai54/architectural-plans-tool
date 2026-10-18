# Architectural Plans Tool

Upload an architectural PDF and turn it into structured, classified geometry you can declutter, calibrate and measure. Runs locally.

## What it does (Phase 0–3)

| Feature | How |
|---|---|
| Extract vector plans | PyMuPDF reads every path (stroke width, colour, fill, dash) and live text. No poppler/pdf2svg needed. |
| Declutter | Paths are classified into toggleable categories: structure, detail, wall hatching, fine lines, solid fills, outlined text. "Clean" shows the building and hides the noise. |
| Separate drawings | A sheet is split into its drawings (plan, each elevation) plus the title block, each with its own scale. |
| Scale | Enter the sheet ratio (right only if printed at full size, e.g. "1:50 @ A1" on an A1 page) or calibrate by clicking two ends of a known length. |
| Measure | Click two points; they snap to line ends and corners. Lengths are real-world mm/m. |
| Rooms | Draw a room by clicking its corners. Area, perimeter, furniture and free floor area are calculated at real scale; rename rooms in the sidebar. |
| Furniture | Catalogue of typical appliances and furniture at true size (editable, plus custom items). Click to place, drag to move, R to rotate, arrow keys to nudge. A dashed zone shows working / door clearance. |
| Resize blocks | Select an item and drag the white edge handles (opposite edge stays fixed, sizes snap to 10 mm), or type exact width, depth and clearance. "Save size as preset" adds it to the catalogue. |
| Review pins | The PDF's own notes are imported as pins (wrapped lines merge into one). Add pins, drag them to the spot they refer to, reply in a thread, resolve or reopen. |
| Options | Each option is its own set of furniture ("stove left" vs "stove right"). Duplicate an option, rename it, and compare items, furniture area, free area and clashes side by side. |
| Accept / reject | Placed items start as proposed (dashed). Accept (solid, ✓) or reject (hidden unless "Show rejected"). "Existing plan only" hides all proposals. |
| Clash checks | Red when an item overlaps a wall, hatched wall band or another item; amber when its front clearance is blocked. |
| Schedule | Room and furniture schedule as CSV, including option and status. |
| Saved | Rooms, furniture and measurements are saved automatically per page. |
| Export | Download the current clean view (including rooms and furniture) as SVG, or the original PDF. |
| Multi-page | Every page is extracted; pick one in the sidebar. |
| Scans | Detected and shown as a raster preview (automatic extraction for scans is not built yet). |

## Setup

Python 3.10+ is the only requirement.

```bash
./run.sh          # installs backend/requirements.txt and serves http://localhost:8000
```

The server binds to 127.0.0.1 only. Uploaded plans and extraction results are stored in `backend/outputs/` (git-ignored).

## Tests

```bash
cd backend
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q
```

## Known limits

- Detection uses geometry, not layers: CAD PDFs usually carry no layer names, and text is often outlined, so room names and dimension text are hidden by "Text (outlined)" rather than read. OCR is planned.
- Leader lines from annotations can remain in the Clean view.
- Rooms are drawn by clicking corners; automatic room detection is not built. Names are typed in (no OCR yet).
- Clash checks use the heavy linework and hatched wall bands as walls. Thin-line features (e.g. fitted units drawn in light weights) are not obstacles.
- Options share rooms and pins; only furniture differs between options.
- Item "front" is one edge with a clearance zone; it does not model door arcs or chair pull-out space.
- The scale ratio cannot be read from the title block (outlined text); set it or calibrate.
- PyMuPDF is AGPL-licensed: fine for local use, but review before distributing this as a product.

## Layout

- `backend/extract.py`: PDF to scene (classification, views, snap points)
- `backend/main.py`: FastAPI app and job storage
- `frontend/index.html`: viewer (vanilla JS, no build step)
