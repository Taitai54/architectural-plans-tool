import os, subprocess, uuid, shutil, base64, json, re
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

OUTPUT_DIR = Path("outputs")
OUTPUT_DIR.mkdir(exist_ok=True)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")


# ── Upload PDF and extract SVG ────────────────────────────────────────────────
@app.post("/upload")
async def upload_pdf(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are supported")

    job_id = str(uuid.uuid4())[:8]
    job_dir = OUTPUT_DIR / job_id
    job_dir.mkdir()

    pdf_path = job_dir / "original.pdf"
    with open(pdf_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    svg_path = job_dir / "extracted.svg"
    result = subprocess.run(
        ["pdf2svg", str(pdf_path), str(svg_path)],
        capture_output=True, text=True
    )
    if result.returncode != 0 or not svg_path.exists():
        raise HTTPException(500, f"pdf2svg failed: {result.stderr}")

    # High-res PNG for AI vision
    subprocess.run(
        ["pdftoppm", "-r", "200", "-png", "-singlefile", str(pdf_path), str(job_dir / "preview")],
        capture_output=True
    )
    for candidate in [job_dir / "preview-1.png"]:
        if candidate.exists():
            candidate.rename(job_dir / "preview.png")

    svg_text = svg_path.read_text()
    vb = re.search(r'viewBox=["\']([^"\']+)["\']', svg_text)
    w  = re.search(r'<svg[^>]+width=["\']([^"\']+)["\']', svg_text)
    h  = re.search(r'<svg[^>]+height=["\']([^"\']+)["\']', svg_text)

    return {
        "job_id": job_id,
        "filename": file.filename,
        "svg_url": f"/file/{job_id}/extracted.svg",
        "preview_url": f"/file/{job_id}/preview.png",
        "viewbox": vb.group(1) if vb else "0 0 842 1190",
        "svg_width": w.group(1) if w else "842",
        "svg_height": h.group(1) if h else "1190",
    }


# ── Serve files ───────────────────────────────────────────────────────────────
@app.get("/file/{job_id}/{filename}")
async def get_file(job_id: str, filename: str):
    path = OUTPUT_DIR / job_id / filename
    if not path.exists():
        raise HTTPException(404, "File not found")
    return FileResponse(path)


# ── Apply AI changes — Vision + Overlay Patch ─────────────────────────────────
class ChangeRequest(BaseModel):
    job_id: str
    change_description: str
    api_key: str = ""
    viewbox: str = "0 0 841.92 1190.52"


@app.post("/apply-changes")
async def apply_changes(req: ChangeRequest):
    job_dir = OUTPUT_DIR / req.job_id
    svg_path = job_dir / "extracted.svg"
    preview_path = job_dir / "preview.png"

    if not svg_path.exists():
        raise HTTPException(404, "SVG not found — upload a PDF first")
    if not preview_path.exists():
        raise HTTPException(404, "Preview image not found")

    api_key = req.api_key or OPENAI_API_KEY
    if not api_key:
        raise HTTPException(400, "No OpenAI API key — add credit at platform.openai.com and paste your key in the sidebar")

    with open(preview_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()

    vb = req.viewbox
    vb_parts = vb.split()
    vb_w = vb_parts[2] if len(vb_parts) >= 4 else "841.92"
    vb_h = vb_parts[3] if len(vb_parts) >= 4 else "1190.52"

    prompt = f"""You are an expert architectural SVG draughtsperson reviewing a floor plan.

The SVG coordinate system: viewBox="{vb}" — origin (0,0) is TOP-LEFT, x increases right, y increases DOWN.
Total drawing size: {vb_w} wide × {vb_h} tall (in points, 1pt ≈ 0.353mm).

REQUESTED CHANGE:
"{req.change_description}"

Study the image carefully. Identify the exact pixel positions of the relevant walls, rooms and spaces,
then map them to SVG coordinates (scale proportionally from the viewBox dimensions).

Return ONLY a JSON object — no markdown, no code fences — with these exact keys:

{{
  "description": "Plain English summary of what was changed",
  "remove_regions": [
    {{"x": 0, "y": 0, "width": 0, "height": 0, "label": "what is removed"}}
  ],
  "add_svg": "<g id='proposed-changes' xmlns='http://www.w3.org/2000/svg'>...SVG elements...</g>"
}}

Styling rules — match the existing drawing exactly:
- New proposed structure outline: stroke="#cc0000" stroke-width="2" fill="none"
- New proposed area fill: diagonal red hatching using <pattern> with 45° lines, stroke="#cc0000" stroke-width="0.8", spacing 8pt
- Demolished/removed walls: shown with stroke="#cc0000" stroke-width="1.5" stroke-dasharray="8,4" (the remove_regions white overlay erases the original)
- New solid walls: stroke="#000000" stroke-width="2" fill="none"
- Room labels: font-family="Arial,sans-serif" font-size="9" fill="#cc0000" text-anchor="middle" font-weight="bold"
- Dimension lines: stroke="#000000" stroke-width="0.7" with short tick marks at ends
- Dimension text: font-family="Arial,sans-serif" font-size="7" fill="#000000"
- All coordinates must fall within: 0 0 {vb_w} {vb_h}"""

    from openai import OpenAI
    client = OpenAI(api_key=api_key)

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}", "detail": "high"}},
                    {"type": "text", "text": prompt}
                ]
            }],
            max_tokens=4000
        )
    except Exception as e:
        err = str(e)
        if "insufficient_quota" in err or "429" in err:
            raise HTTPException(402, "OpenAI quota exceeded — add credit at platform.openai.com/account/billing")
        raise HTTPException(500, f"AI error: {err}")

    raw = response.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    try:
        patch = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        if m:
            patch = json.loads(m.group())
        else:
            raise HTTPException(500, f"AI returned unparseable response: {raw[:400]}")

    svg_text = svg_path.read_text()

    removal_svg = ""
    for r in patch.get("remove_regions", []):
        x, y, rw, rh = r.get("x",0), r.get("y",0), r.get("width",0), r.get("height",0)
        if rw > 0 and rh > 0:
            removal_svg += f'<rect x="{x}" y="{y}" width="{rw}" height="{rh}" fill="white" stroke="none"/>\n'
            cx, cy = x + rw/2, y + rh/2
            removal_svg += f'<line x1="{x}" y1="{cy}" x2="{x+rw}" y2="{cy}" stroke="#cc0000" stroke-width="1.5" stroke-dasharray="8,4"/>\n'

    add_svg  = patch.get("add_svg", "")
    desc     = patch.get("description", "Changes applied")
    patch_block = f'\n<!-- AI PATCH: {desc} -->\n{removal_svg}\n{add_svg}\n'
    updated_svg = svg_text.rsplit("</svg>", 1)[0] + patch_block + "</svg>"

    (job_dir / "updated.svg").write_text(updated_svg)
    (job_dir / "patch.json").write_text(json.dumps(patch, indent=2))

    return {
        "job_id": req.job_id,
        "updated_svg_url": f"/file/{req.job_id}/updated.svg",
        "description": desc,
    }


# ── Health check ──────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok", "pdf2svg": shutil.which("pdf2svg") is not None}


# ── Demo patch (no API key needed) ────────────────────────────────────────────
class DemoRequest(BaseModel):
    job_id: str

@app.post("/demo-patch")
async def demo_patch(req: DemoRequest):
    job_dir = OUTPUT_DIR / req.job_id
    svg_path = job_dir / "extracted.svg"
    if not svg_path.exists():
        raise HTTPException(404, "SVG not found — upload a PDF first")

    svg_text = svg_path.read_text()
    vb = re.search(r'viewBox=["\']([^"\']+)["\']', svg_text)
    vb_parts = (vb.group(1) if vb else "0 0 841.92 1190.52").split()
    vb_w, vb_h = float(vb_parts[2]), float(vb_parts[3])

    cx = vb_w * 0.50
    cy = vb_h * 0.68
    rw, rh = vb_w * 0.22, vb_h * 0.10

    hatch_id = "demo-red-hatch"
    add_svg = f"""<defs>
  <pattern id="{hatch_id}" patternUnits="userSpaceOnUse" width="8" height="8" patternTransform="rotate(45)">
    <line x1="0" y1="0" x2="0" y2="8" stroke="#cc0000" stroke-width="0.8"/>
  </pattern>
</defs>
<g id="proposed-changes-demo">
  <rect x="{cx - rw/2:.2f}" y="{cy - rh/2:.2f}" width="{rw:.2f}" height="{rh:.2f}"
        fill="url(#{hatch_id})" stroke="#cc0000" stroke-width="2"/>
  <text x="{cx:.2f}" y="{cy - 6:.2f}" font-family="Arial,sans-serif" font-size="9"
        text-anchor="middle" fill="#cc0000" font-weight="bold">PROPOSED</text>
  <text x="{cx:.2f}" y="{cy + 6:.2f}" font-family="Arial,sans-serif" font-size="9"
        text-anchor="middle" fill="#cc0000" font-weight="bold">GARDEN ROOM EXTENSION</text>
  <line x1="{cx - rw/2 - 15:.2f}" y1="{cy - rh/2:.2f}" x2="{cx + rw/2 + 15:.2f}" y2="{cy - rh/2:.2f}"
        stroke="#000000" stroke-width="0.7"/>
  <line x1="{cx - rw/2 - 15:.2f}" y1="{cy + rh/2:.2f}" x2="{cx + rw/2 + 15:.2f}" y2="{cy + rh/2:.2f}"
        stroke="#000000" stroke-width="0.7"/>
  <text x="{cx:.2f}" y="{cy + rh/2 + 12:.2f}" font-family="Arial,sans-serif" font-size="7"
        text-anchor="middle" fill="#000000">{rw * 0.353 / 1000:.1f}m</text>
</g>"""

    desc = "Demo: proposed garden room extension overlay"
    patch_block = f'\n<!-- AI PATCH: {desc} -->\n\n{add_svg}\n'
    updated_svg = svg_text.rsplit("</svg>", 1)[0] + patch_block + "</svg>"

    (job_dir / "updated.svg").write_text(updated_svg)

    return {
        "job_id": req.job_id,
        "updated_svg_url": f"/file/{req.job_id}/updated.svg",
        "description": desc,
    }


# ── Serve frontend ────────────────────────────────────────────────────────────
frontend_path = Path(__file__).parent.parent / "frontend"
if frontend_path.exists():
    app.mount("/", StaticFiles(directory=str(frontend_path), html=True), name="frontend")
