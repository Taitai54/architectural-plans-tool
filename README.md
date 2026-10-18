# Architectural Plans Tool

Upload an architectural PDF and turn it into structured, classified geometry you can declutter, calibrate and measure. Runs locally.

## What it does (Phase 0–5)

| Feature | How |
|---|---|
| Extract vector plans | PyMuPDF reads every path (stroke width, colour, fill, dash) and live text. No poppler/pdf2svg needed. |
| Declutter | Paths are classified into toggleable categories: structure, detail, wall hatching, fine lines, solid fills, outlined text. "Clean" shows the building and hides the noise. |
| Separate drawings | A sheet is split into its drawings (plan, each elevation) plus the title block, each with its own scale. |
| Scale | Enter the sheet ratio (right only if printed at full size, e.g. "1:50 @ A1" on an A1 page) or calibrate by clicking two ends of a known length. |
| Measure | Click two points; they snap to line ends and corners. Lengths are real-world mm/m. |
| Detect rooms | One click finds the rooms: walls, wall hatching, windows and door swings form closed regions; open-plan areas are split between the room names read from the drawing. Each region comes with a name and area, and you tick the ones to keep. Edges inside open spaces are marked "estimated". |
| Read the drawing | Room names, door tags and the sheet scale are drawn as outlines in CAD PDFs. With the OCR add-on they are read, and a printed "1:50 @ A1" is applied automatically when the page really is that size. |
| Rooms | Draw a room by clicking its corners. Area, perimeter, furniture and free floor area are calculated at real scale; rename rooms in the sidebar. |
| Furniture | Catalogue of typical appliances and furniture at true size (editable, plus custom items). Click to place, drag to move, R to rotate, arrow keys to nudge. A dashed zone shows working / door clearance. |
| Resize blocks | Select an item and drag the white edge handles (opposite edge stays fixed, sizes snap to 10 mm), or type exact width, depth and clearance. "Save size as preset" adds it to the catalogue. |
| Review pins | The PDF's own notes are imported as pins (wrapped lines merge into one). Add pins, drag them to the spot they refer to, reply in a thread, resolve or reopen. |
| Options | Each option is its own set of furniture ("stove left" vs "stove right"). Duplicate an option, rename it, and compare items, furniture area, free area and clashes side by side. |
| Accept / reject | Placed items start as proposed (dashed). Accept (solid, ✓) or reject (hidden unless "Show rejected"). "Existing plan only" hides all proposals. |
| AI assistant | Type a request ("put the fridge against the left wall of the kitchen", "move the cooker to the right wall"). The assistant calls typed tools that refer to rooms, walls and items by name; the server resolves them to real positions, checks them for clashes, and returns **pending proposals** (marked ✦) with Accept / Undo. It never writes raw coordinates. |
| Providers | Claude, OpenAI or Gemini through each provider's official SDK, plus an offline demo parser (simple phrases only, no key needed). Keys stay on the server. |
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

On Windows, double-click `run.bat` instead. It installs the requirements, starts the server and opens the browser.

The server binds to 127.0.0.1 only. Uploaded plans and extraction results are stored in `backend/outputs/` (git-ignored).

## OCR setup (room names and automatic scale)

```bash
pip install -r backend/requirements-ocr.txt      # rapidocr-onnxruntime: pure pip, no system packages
```

Without it, room detection still works but rooms are unnamed and the scale is not read. The first "Detect rooms" on a page reads the text (about 10 seconds for an A1 sheet) and caches it; later runs take about 3 seconds.

## AI setup

The demo parser works with no setup. For a real assistant, install the SDK for the provider you use and add its key to `backend/.env` (git-ignored; the browser never sees keys):

```bash
pip install -r backend/requirements-ai.txt        # anthropic, openai, google-genai (install only what you need)
```

```
# backend/.env  (any one is enough)
ANTHROPIC_API_KEY=...
OPENAI_API_KEY=...
GEMINI_API_KEY=...
# optional overrides
AI_MODEL_ANTHROPIC=claude-opus-5-5
AI_MODEL_OPENAI=gpt-4o
AI_MODEL_GEMINI=gemini-2.5-pro
AI_EFFORT=medium
```

Restart the server, then pick the provider and model next to the chat box. The model box is a dropdown of suggestions that also accepts any model name you type.

**Local models (Llama etc.)**: choose "Local model" in the provider list. No key needed. It talks to any OpenAI-compatible server on your machine and lists the models it has installed.

```
# Ollama (default, nothing to configure):  ollama pull llama3.1   then pick "llama3.1" in the model box
# LM Studio / llama.cpp server: add to backend/.env
LOCAL_LLM_BASE_URL=http://localhost:1234/v1
```

The model must support tool calling (Llama 3.1 or newer, Qwen 2.5, Mistral Nemo). Small models are weaker at placing things precisely, so check their proposals before accepting.

How it works: `backend/planner.py` holds the tools (`find_space`, `place_item`, `move_item`, `resize_item`, `add_pin`, `move_pin`, `reply_pin`) and the geometry checks; `backend/ai.py` is one provider-neutral agent loop (max 6 rounds) with thin adapters. If a proposal clashes, the problem is returned to the model so it can retry; clearance warnings are reported but allowed. Moving or resizing an existing item is a pending replacement: accepting it retires the old one.

## Tests

```bash
cd backend
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q
```

## Known limits

- Detection depends on closed walls and drawn door swings. Plans with open archways rely on the OCR'd names to split the space, and the split edge is a guess. Small unnamed regions (lobbies, window reveals) are listed but not pre-selected. A WC label that OCR misses leaves that room unnamed.
- Wall names (top/bottom/left/right) pick the single longest matching edge, so L-shaped rooms only expose one edge per side. Each request may propose at most 40 changes, and `find_space` stops after 8 seconds.
- The AI adapters are tested against fake clients and the SDKs' own types, not live APIs: confirm each provider once with a real key. The assistant cannot delete items, draw rooms or edit walls, and has no vision of the drawing: it works from the extracted rooms, walls and items.

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
