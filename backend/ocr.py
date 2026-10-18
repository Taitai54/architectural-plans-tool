"""Reading the text that CAD exports draw as outlines (room names, door tags, the scale in the title block).

Uses rapidocr-onnxruntime when installed (pure pip, no system binary); everything here degrades
gracefully without it. Results are plain dicts so they can be cached with the page.
"""
import importlib.util
import re
import statistics
from typing import Any, Callable

TILE_W, TILE_H = 1500.0, 1000.0     # pt per OCR tile
TILE_OVERLAP = 80.0
DPI = 150
MIN_CONF = 0.45

ROOM_WORDS = {
    "kitchen", "hall", "hallway", "entrance", "entr", "bedroom", "bed", "bathroom", "bath", "shower", "wc", "toilet", "cloakroom",
    "cloaks", "clks", "utility", "pantry", "garage", "lounge", "living", "sitting", "dining", "study", "office", "landing",
    "ensuite", "en-suite", "store", "storage", "reception", "porch", "conservatory", "cupboard", "wardrobe", "laundry", "snug",
    "playroom", "cellar", "loft", "garden", "room", "annexe", "annex", "extension", "workshop", "summerhouse", "outbuilding", "shed", "sunroom", "orangery", "nook", "family", "tv", "boot", "plant", "gym", "studio", "nursery", "dressing", "stairs", "vestibule",
}
NOT_ROOM = re.compile(
    r"^(step|new path|existing|svp|rwp|r\.e\.?|rooflight|ceiling slope|slot drain|coffee station|masonry removed|hearth|stove|"
    r"sliding shading panel|line of fascia|decorative brickwork|cl|sd|hd|ef|up|dn|down|north|plan|section|elevation)\b", re.I)
TAG = re.compile(r"^[A-Z]{1,3}\s?\d{1,3}[a-z]?$")          # D01, W02, ED01
SCALE_RE = re.compile(r"(?<!\d)1\s*(?::|/(?=\s*\d{1,4}\s*@))\s*(\d{1,4})(?!\d)(?:\s*@\s*(A[0-4]))?", re.I)
PAPERS = {"A0": (841, 1189), "A1": (594, 841), "A2": (420, 594), "A3": (297, 420), "A4": (210, 297)}


def available() -> bool:
    return importlib.util.find_spec("rapidocr_onnxruntime") is not None


_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR
        _engine = RapidOCR()
    return _engine


def _tiles(rect: tuple[float, float, float, float]):
    x0, y0, x1, y1 = rect
    nx = max(1, int(-(-(x1 - x0 - TILE_OVERLAP) // (TILE_W - TILE_OVERLAP))))
    ny = max(1, int(-(-(y1 - y0 - TILE_OVERLAP) // (TILE_H - TILE_OVERLAP))))
    w, h = (x1 - x0) / nx, (y1 - y0) / ny
    for i in range(nx):
        for j in range(ny):
            yield (x0 + i * w - (TILE_OVERLAP / 2 if i else 0), y0 + j * h - (TILE_OVERLAP / 2 if j else 0),
                   x0 + (i + 1) * w + (TILE_OVERLAP / 2 if i < nx - 1 else 0), y0 + (j + 1) * h + (TILE_OVERLAP / 2 if j < ny - 1 else 0))


def _dedupe(items: list[dict]) -> list[dict]:
    keep: list[dict] = []
    for it in sorted(items, key=lambda i: -i["conf"]):
        cx, cy = (it["bbox"][0] + it["bbox"][2]) / 2, (it["bbox"][1] + it["bbox"][3]) / 2
        dup = False
        for k in keep:
            kx, ky = (k["bbox"][0] + k["bbox"][2]) / 2, (k["bbox"][1] + k["bbox"][3]) / 2
            if abs(cx - kx) < 6 and abs(cy - ky) < 6:
                dup = True
                break
        if not dup:
            keep.append(it)
    return keep


def ocr_page(pdf_path: str, page_no: int, regions: list[tuple[float, float, float, float]],
             engine: Callable | None = None) -> list[dict]:
    """OCR the given page regions (PDF points). Returns [{text, conf, bbox:[x0,y0,x1,y1]}]."""
    import numpy as np
    import pymupdf
    eng = engine or _get_engine()
    doc = pymupdf.open(pdf_path)
    try:
        page = doc[page_no - 1]
        if page.rotation:
            page.set_rotation(0)
        zoom = DPI / 72.0
        out: list[dict] = []
        for region in regions:
            for tx0, ty0, tx1, ty1 in _tiles(region):
                clip = pymupdf.Rect(tx0, ty0, tx1, ty1) & page.rect
                if clip.is_empty:
                    continue
                pix = page.get_pixmap(clip=clip, dpi=DPI, colorspace=pymupdf.csRGB)
                img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3]
                res, _ = eng(img)
                for box, text, conf in (res or []):
                    conf = float(conf)
                    text = str(text).strip()
                    if not text or conf < MIN_CONF:
                        continue
                    xs, ys = [p[0] for p in box], [p[1] for p in box]
                    out.append({"text": text, "conf": round(conf, 2),
                                "bbox": [round(clip.x0 + min(xs) / zoom, 1), round(clip.y0 + min(ys) / zoom, 1),
                                         round(clip.x0 + max(xs) / zoom, 1), round(clip.y0 + max(ys) / zoom, 1)]})
        return _dedupe(out)
    finally:
        doc.close()


def scale_candidates(items: list[dict]) -> list[dict]:
    """Every "1:50" / "1:50 @ A1" in the text with where it sits: [{ratio, paper, text, conf, bbox}]."""
    out = []
    for it in items:
        m = SCALE_RE.search(it["text"])
        if not m:
            continue
        ratio = int(m.group(1))
        if 5 <= ratio <= 5000:
            out.append({"ratio": ratio, "paper": (m.group(2) or "").upper() or None, "text": it["text"].strip(),
                        "conf": it.get("conf", 1.0), "bbox": it["bbox"]})
    return out


def parse_scale(items: list[dict], page_w_pt: float | None = None, page_h_pt: float | None = None) -> dict | None:
    """Find "1:50" / "1:50 @ A1" in the text. Returns {ratio, paper, text, page_matches_paper} or None."""
    cands = []
    for it in items:
        m = SCALE_RE.search(it["text"])
        if not m:
            continue
        ratio = int(m.group(1))
        if ratio < 5 or ratio > 5000:
            continue
        cands.append({"ratio": ratio, "paper": (m.group(2) or "").upper() or None, "text": it["text"].strip(), "conf": it.get("conf", 1.0)})
    if not cands:
        return None
    cands.sort(key=lambda c: (c["paper"] is None, -c["conf"]))    # "1:50 @ A1" beats a bare ratio
    best = cands[0]
    match = None
    if best["paper"] and page_w_pt and page_h_pt:
        lo, hi = sorted((page_w_pt * 25.4 / 72, page_h_pt * 25.4 / 72))
        pw, ph = PAPERS[best["paper"]]
        match = abs(lo - pw) < 6 and abs(hi - ph) < 6
    return {**best, "page_matches_paper": match}


STOP_WORDS = {"draft", "elevation", "section", "plan", "floor", "ceiling", "scale", "drawing", "number", "status", "client",
              "project", "date", "revision", "existing", "rooflight", "chartered", "architects", "brierley", "groom", "riba"}


def room_labels(items: list[dict]) -> list[dict]:
    """Pick the OCR items that look like room names: horizontal words, known room words or larger than the usual annotation text."""
    heights = [i["bbox"][3] - i["bbox"][1] for i in items if len(i["text"]) >= 3]
    med = statistics.median(heights) if heights else 0
    out = []
    for it in items:
        text = " ".join(it["text"].split())
        if not re.search(r"[A-Za-z]{2}", text) or len(text) > 32 or TAG.match(text) or NOT_ROOM.match(text):
            continue
        words = [w.strip(".,").lower() for w in re.split(r"[\s/,&]+", text) if w]
        if set(words) & STOP_WORDS:
            continue
        w, h = it["bbox"][2] - it["bbox"][0], it["bbox"][3] - it["bbox"][1]
        if w < 1.2 * h:                                  # vertical text (dimensions, rotated notes) is not a room name
            continue
        known = bool(set(words) & ROOM_WORDS)
        digits = re.sub(r"[A-Za-z .,'&/-]", "", text)
        if digits and not (known and re.fullmatch(r"\d{1,2}", digits)):    # "Reception 2" is fine, "1350" or "D05" are not
            continue
        large = med > 0 and h >= 1.45 * med
        if known or large:
            out.append({"text": text, "x": (it["bbox"][0] + it["bbox"][2]) / 2, "y": (it["bbox"][1] + it["bbox"][3]) / 2,
                        "h": h, "known": known, "conf": it.get("conf", 1.0)})
    return out
