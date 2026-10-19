import contextlib
import json
import re
import shutil
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Literal

import pymupdf
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field

import ai
import autodetect
import ocr
import extract
from catalogue import CATALOGUE
from planner import World

# Local-only tool: no CORS middleware (the frontend is served from the same origin),
# and run.sh binds to 127.0.0.1.
ai.load_env_file(Path(__file__).parent / ".env")   # API keys stay on the server
app = FastAPI()
# Only answer requests addressed to this machine (blocks DNS-rebinding from a web page), and refuse writes whose Origin is another site.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "::1", "testserver"])
MAX_STATE_BYTES = 5_000_000

OUTPUT_DIR = Path(__file__).parent / "outputs"
OUTPUT_DIR.mkdir(exist_ok=True)

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")
VIEW_ID_RE = re.compile(r"^v[0-9]{1,3}$")
PT_TO_MM = 25.4 / 72.0


@app.exception_handler(RequestValidationError)
async def invalid_request(request, exc):
    """Report what was wrong without echoing the input back (it may contain NaN, which JSON cannot carry)."""
    return JSONResponse({"detail": [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]}, status_code=422)


@app.exception_handler(Exception)
async def unexpected_error(request, exc):
    """Show the real error in the page (JSON) and keep the traceback in the terminal."""
    import traceback
    traceback.print_exception(exc)
    return JSONResponse({"detail": f"Server error ({type(exc).__name__}): {ai.scrub(str(exc))[:300]}"}, status_code=500)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin and origin.split("://", 1)[-1].split("/", 1)[0] != request.headers.get("host", ""):
            return JSONResponse({"detail": "Cross-site request refused"}, status_code=403)
        if request.url.path.endswith("/state") and int(request.headers.get("content-length") or 0) > MAX_STATE_BYTES:
            return JSONResponse({"detail": "Project data is too large"}, status_code=413)
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


def job_dir(job_id: str) -> Path:
    if not JOB_ID_RE.match(job_id):
        raise HTTPException(404, "Job not found")
    d = (OUTPUT_DIR / job_id).resolve()
    if not d.is_relative_to(OUTPUT_DIR.resolve()) or not d.is_dir():
        raise HTTPException(404, "Job not found")
    return d


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


_JOB_LOCKS: dict[str, threading.RLock] = {}
_JOB_LOCKS_GUARD = threading.Lock()


@contextlib.contextmanager
def job_lock(d: Path):
    """Serialises read-modify-write of a job's files (two quick requests must not overwrite each other's change)."""
    with _JOB_LOCKS_GUARD:
        lock = _JOB_LOCKS.setdefault(str(d), threading.RLock())
    with lock:
        yield


def write_json(path: Path, data) -> None:
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    try:
        for attempt in range(8):
            try:
                tmp.replace(path)
                return
            except PermissionError:            # Windows: the target is briefly open elsewhere (reader, antivirus, indexer)
                if attempt == 7:
                    raise
                time.sleep(0.03 * (attempt + 1))
    finally:
        tmp.unlink(missing_ok=True)


# ── Upload and extract ────────────────────────────────────────────────────────
@app.post("/api/upload")
def upload_pdf(file: UploadFile = File(...)):
    """Plain `def` so FastAPI runs the CPU-bound extraction in a threadpool."""
    name = file.filename or "plan.pdf"
    if not name.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are supported")

    job_id = uuid.uuid4().hex
    d = OUTPUT_DIR / job_id
    d.mkdir()
    try:
        pdf_path = d / "original.pdf"
        size = 0
        with open(pdf_path, "wb") as f:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "PDF is larger than 50 MB")
                f.write(chunk)
        with open(pdf_path, "rb") as f:
            if f.read(5) != b"%PDF-":
                raise HTTPException(400, "That file is not a valid PDF")

        try:
            pages = extract.extract_pdf(str(pdf_path))
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:  # corrupt PDFs surface as assorted pymupdf errors
            raise HTTPException(400, f"Could not read PDF: {e}")

        meta = {"job_id": job_id, "filename": name, "pages": []}
        for i, pg in enumerate(pages):
            write_json(d / f"page-{i + 1}.json", pg)
            meta["pages"].append({
                "page": i + 1, "kind": pg["kind"], "width": pg["width"], "height": pg["height"],
                "views": {v["id"]: {"name": v["name"], "mm_per_pt": None, "ratio": None}
                          for v in pg["views"]},
            })
        write_json(d / "meta.json", meta)
    except BaseException:
        shutil.rmtree(d, ignore_errors=True)
        raise
    return summary(meta, pages)


def summary(meta: dict, pages: list[dict]) -> dict:
    return {
        "job_id": meta["job_id"],
        "filename": meta["filename"],
        "pages": [
            {"page": m["page"], "kind": m["kind"], "width": m["width"], "height": m["height"],
             "paths": sum(pg["counts"].values()), "views": len(pg["views"])}
            for m, pg in zip(meta["pages"], pages)
        ],
    }


# ── Read back ─────────────────────────────────────────────────────────────────
@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    d = job_dir(job_id)
    meta = read_json(d / "meta.json")
    pages = [read_json(d / f"page-{m['page']}.json") for m in meta["pages"]]
    return summary(meta, pages)


def _load_scene(d: Path, page: int) -> dict:
    """Extracted scene for a page with the user's view names and scales merged in."""
    meta = read_json(d / "meta.json")
    pm = next((m for m in meta["pages"] if m["page"] == page), None)
    if pm is None:
        raise HTTPException(404, "Page not found")
    scene = read_json(d / f"page-{page}.json")
    for v in scene["views"]:
        saved = pm["views"].get(v["id"], {})
        v["name"] = saved.get("name") or v["name"]
        v["mm_per_pt"] = saved.get("mm_per_pt")
        v["ratio"] = saved.get("ratio")
    return scene


@app.get("/api/jobs/{job_id}/pages/{page}")
def get_page(job_id: str, page: int):
    d = job_dir(job_id)
    scene = _load_scene(d, page)
    scene["categories"] = [{"id": k, "label": label} for k, label in extract.CATEGORIES]
    sp = _state_path(d, page)
    scene["state"] = read_json(sp) if sp.exists() else PageState().model_dump()
    return scene


class ViewUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    # Real millimetres represented by one PDF point (from a two-point calibration)...
    mm_per_pt: float | None = Field(default=None, gt=0, lt=100000)
    # ...or the drawing scale "1:ratio" for a sheet printed at true size.
    ratio: float | None = Field(default=None, gt=0, lt=100000)


@app.put("/api/jobs/{job_id}/pages/{page}/views/{view_id}")
def update_view(job_id: str, page: int, view_id: str, body: ViewUpdate):
    d = job_dir(job_id)
    if not VIEW_ID_RE.match(view_id):
        raise HTTPException(404, "View not found")
    with job_lock(d):
        meta = read_json(d / "meta.json")
        pm = next((m for m in meta["pages"] if m["page"] == page), None)
        if pm is None or view_id not in pm["views"]:
            raise HTTPException(404, "View not found")
        v = pm["views"][view_id]
        if body.name is not None:
            v["name"] = body.name.strip() or v["name"]
        if body.ratio is not None:
            v["ratio"] = body.ratio
            v["mm_per_pt"] = PT_TO_MM * body.ratio
        elif body.mm_per_pt is not None:
            v["ratio"] = None
            v["mm_per_pt"] = body.mm_per_pt
        write_json(d / "meta.json", meta)
        return v


# ── Project state: rooms, furniture, measurements (user data, separate from extraction) ──
class Pt(BaseModel):
    x: float = Field(ge=-1e6, le=1e6)
    y: float = Field(ge=-1e6, le=1e6)


class Room(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")
    name: str = Field(max_length=80)
    poly: list[Pt] = Field(min_length=3, max_length=300)


class Item(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")
    kind: str = Field(max_length=40)
    name: str = Field(max_length=80)
    w: float = Field(gt=0, le=50000)       # millimetres, across the front
    d: float = Field(gt=0, le=50000)       # millimetres, front to back
    clear: float = Field(ge=0, le=10000)   # working / door clearance in front, mm
    option: str = Field(default="", max_length=40)   # which design option the item belongs to
    status: Literal["pending", "accepted", "rejected"] = "pending"
    source: Literal["user", "ai"] = "user"
    note: str = Field(default="", max_length=500)    # why the assistant proposed it
    replaces: str = Field(default="", max_length=40)  # id of the item this proposal supersedes when accepted
    x: float = Field(ge=-1e6, le=1e6)      # centre, PDF points
    y: float = Field(ge=-1e6, le=1e6)
    rot: float = Field(ge=-360, le=360)    # degrees, multiples of 90 in the UI


class Measure(BaseModel):
    a: Pt
    b: Pt


class CatalogueItem(BaseModel):
    kind: str = Field(max_length=40)
    name: str = Field(max_length=80)
    w: float = Field(gt=0, le=50000)
    d: float = Field(gt=0, le=50000)
    clear: float = Field(ge=0, le=10000)


class Comment(BaseModel):
    text: str = Field(max_length=4000)
    ts: float = Field(default=0, ge=0, le=1e15, allow_inf_nan=False)


class Pin(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")
    x: float = Field(ge=-1e6, le=1e6)
    y: float = Field(ge=-1e6, le=1e6)
    text: str = Field(max_length=4000)
    status: Literal["open", "resolved"] = "open"
    source: Literal["pdf", "user", "ai"] = "user"
    created: float = Field(default=0, ge=0, le=1e15, allow_inf_nan=False)
    thread: list[Comment] = Field(default_factory=list, max_length=200)


class Option(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")
    name: str = Field(max_length=80)


class PageState(BaseModel):
    pins: list[Pin] = Field(default_factory=list, max_length=500)
    options: list[Option] = Field(default_factory=list, max_length=20)
    active_option: str = Field(default="", max_length=40)
    notes_imported: bool = False
    rooms: list[Room] = Field(default_factory=list, max_length=200)
    items: list[Item] = Field(default_factory=list, max_length=1000)
    measures: list[Measure] = Field(default_factory=list, max_length=500)
    custom_items: list[CatalogueItem] = Field(default_factory=list, max_length=100)


def _state_path(d: Path, page: int) -> Path:
    return d / f"state-{page}.json"


def _page_meta(d: Path, page: int) -> dict:
    meta = read_json(d / "meta.json")
    pm = next((m for m in meta["pages"] if m["page"] == page), None)
    if pm is None:
        raise HTTPException(404, "Page not found")
    return pm


@app.put("/api/jobs/{job_id}/pages/{page}/state")
def put_state(job_id: str, page: int, body: PageState):
    d = job_dir(job_id)
    _page_meta(d, page)
    with job_lock(d):
        write_json(_state_path(d, page), body.model_dump())
    return {"ok": True}


# ── AI assistant ──────────────────────────────────────────────────────────────
class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    text: str = Field(max_length=2000)


class AIRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)
    provider: str = Field(default="demo", pattern=r"^[a-z]{2,20}$")
    model: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$")
    state: PageState
    history: list[ChatTurn] = Field(default_factory=list, max_length=12)


def validate_ops(ops: list[dict]) -> tuple[list[dict], int]:
    """Keep only proposals that would be valid saved state, so one bad op can never break autosave."""
    good, dropped = [], 0
    for op in ops:
        try:
            if op["op"] == "add_item":
                Item(**op["item"])
            elif op["op"] == "add_pin":
                Pin(**op["pin"])
            elif op["op"] == "reply_pin":
                Comment(text=op["text"])
            elif op["op"] != "move_pin":
                raise ValueError("unknown op")
            good.append(op)
        except Exception:
            dropped += 1
    return good, dropped


@app.get("/api/catalogue")
def get_catalogue():
    return CATALOGUE


@app.get("/api/ai/providers")
def ai_providers():
    return ai.provider_status()      # booleans only: keys are never sent to the browser


@app.get("/api/ai/providers/{provider_id}/models")
def ai_models(provider_id: str):
    try:
        return ai.list_models(provider_id)
    except ai.AIError as e:
        raise HTTPException(404, str(e))


@app.post("/api/jobs/{job_id}/pages/{page}/ai")
def ai_assist(job_id: str, page: int, body: AIRequest):
    d = job_dir(job_id)
    scene = _load_scene(d, page)
    state = body.state.model_dump()
    option = state.get("active_option") or (state["options"][0]["id"] if state.get("options") else "")
    world = World(scene, state, option)
    try:
        provider = ai.make_provider(body.provider, body.model, world)
        result = ai.run_agent(provider, world, body.prompt, [t.model_dump() for t in body.history])
    except ai.AIError as e:
        raise HTTPException(400, ai.scrub(str(e)))
    except Exception as e:           # provider/network failures: report, without leaking keys
        hint = " Is your local model server running (Ollama / LM Studio), and does the model support tools?" if body.provider == "local" else ""
        raise HTTPException(502, f"The AI provider request failed ({type(e).__name__}): {ai.scrub(str(e))[:300]}.{hint}")
    ops, dropped = validate_ops(result["ops"])
    message = result["message"] + (f" ({dropped} invalid proposal(s) were dropped.)" if dropped else "")
    return {**result, "ops": ops, "message": message, "provider": body.provider, "model": provider.model}


def _apply_page_scale(d: Path, page: int, scene: dict, ratios: dict[str, float]) -> None:
    """Store {view_id: ratio} on drawings that have no scale yet (the title block never gets one)."""
    with job_lock(d):
        meta = read_json(d / "meta.json")
        pm = next(m for m in meta["pages"] if m["page"] == page)
        kinds = {v["id"]: v["kind"] for v in scene["views"]}
        for vid, v in pm["views"].items():
            if vid in ratios and not v.get("mm_per_pt") and kinds.get(vid) == "drawing":
                v["ratio"], v["mm_per_pt"] = ratios[vid], PT_TO_MM * ratios[vid]
        write_json(d / "meta.json", meta)


@app.post("/api/jobs/{job_id}/pages/{page}/read-scale")
def read_scale(job_id: str, page: int):
    """Look for a printed scale ("1:50 @ A1") in the drawing's text and apply it when the page size confirms it."""
    d = job_dir(job_id)
    scene = _load_scene(d, page)
    if scene["kind"] == "scan":
        raise HTTPException(400, "This page is a scan, so there is no text to read a scale from.")
    try:
        items, info = autodetect.text_items(d, page, scene)
    except Exception as e:
        raise HTTPException(503, f"Reading the drawing text failed ({type(e).__name__}); try again.")
    plan = autodetect.assign_scales(scene, items)
    found, applied = plan["found"], False
    if plan["ratios"]:
        _apply_page_scale(d, page, scene, plan["ratios"])
        applied = True
    return {"found": found, "applied": applied, "ratios": plan["ratios"], "labelled": plan["labelled"], "ocr_available": info["available"]}


@app.post("/api/jobs/{job_id}/pages/{page}/detect-rooms")
def detect_rooms(job_id: str, page: int):
    """Propose rooms (with names). Rooms are saved only when the user accepts them; a scale read from the drawing is stored on the drawings straight away."""
    d = job_dir(job_id)
    scene = _load_scene(d, page)
    if scene["kind"] == "scan":
        raise HTTPException(400, "This page is a scan, so there are no walls to detect rooms from.")

    def apply_scale(ratios: dict) -> None:
        _apply_page_scale(d, page, scene, ratios)

    try:
        result = autodetect.run(d, page, scene, apply_scale)
    except autodetect.TextReadError as e:
        raise HTTPException(503, str(e))
    if result.get("error") == "scale":
        return JSONResponse({"detail": "Set the scale for the plan first (section 4), then detect rooms.",
                             "scale_suggestion": result["scale_suggestion"]}, status_code=409)
    for r in result["rooms"]:
        r["poly"] = [{"x": x, "y": y} for x, y in r["poly"]]
    return result


@app.get("/api/jobs/{job_id}/original.pdf")
def get_original(job_id: str):
    d = job_dir(job_id)
    return FileResponse(d / "original.pdf", media_type="application/pdf",
                        headers={"Content-Disposition": "attachment; filename=original.pdf"})


@app.get("/api/jobs/{job_id}/pages/{page}/preview.png")
def get_preview(job_id: str, page: int, dpi: int = 100):
    """Raster of the page, used for scanned PDFs that have no vector content."""
    d = job_dir(job_id)
    dpi = max(30, min(dpi, 200))
    doc = pymupdf.open(d / "original.pdf")
    try:
        if not 1 <= page <= doc.page_count:
            raise HTTPException(404, "Page not found")
        pg = doc[page - 1]
        pg.set_rotation(0)  # same frame as the extracted geometry
        png = pg.get_pixmap(dpi=dpi).tobytes("png")
    finally:
        doc.close()
    return Response(png, media_type="image/png")


@app.get("/health")
def health():
    return {"status": "ok", "pymupdf": pymupdf.__version__}


@app.exception_handler(HTTPException)
async def http_error(request, exc: HTTPException):
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


# ── Serve frontend ────────────────────────────────────────────────────────────
frontend_path = Path(__file__).parent.parent / "frontend"
if frontend_path.exists():
    app.mount("/", StaticFiles(directory=str(frontend_path), html=True), name="frontend")
