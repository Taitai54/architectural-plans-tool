"""Orchestration for automatic room detection: read the text once (cached), find the scale, detect rooms."""
import json
import uuid
import math
import re
from pathlib import Path
from typing import Any

import ocr
import rooms


class TextReadError(Exception):
    """Reading the drawing text (live text or OCR) failed."""

PT_TO_MM = 25.4 / 72.0
PAD = 90.0


def text_items(job_dir: Path, page: int, scene: dict) -> tuple[list[dict], dict[str, Any]]:
    """Live text plus OCR of the outlined text, cached per page. Degrades to live text when OCR isn't installed."""
    info: dict[str, Any] = {"available": ocr.available(), "ran": False, "cached": False}
    cache = job_dir / f"ocr-{page}.json"
    items: list[dict] = []
    if cache.exists():
        try:
            loaded = json.loads(cache.read_text(encoding="utf-8"))
            items = loaded if isinstance(loaded, list) else []
        except ValueError:
            items = []
        info["cached"] = bool(items)
    if not items and info["available"]:
        w, h = scene["width"], scene["height"]
        regions = [(max(0.0, v["bbox"][0] - PAD), max(0.0, v["bbox"][1] - PAD), min(w, v["bbox"][2] + PAD), min(h, v["bbox"][3] + PAD))
                   for v in scene["views"]] or [(0.0, 0.0, w, h)]
        items = ocr.ocr_page(str(job_dir / "original.pdf"), page, regions)
        if items:                                   # an empty result is not worth remembering
            tmp = cache.with_name(f"{cache.name}.{uuid.uuid4().hex}.tmp")
            tmp.write_text(json.dumps(items), encoding="utf-8")
            tmp.replace(cache)
        info["ran"] = True
    live = [{"text": t["text"], "conf": 1.0, "bbox": t["bbox"]} for t in scene.get("texts", [])]
    merged = ocr._dedupe(items + live)
    info["items"] = len(merged)
    return merged, info


NEAR_PT = 90.0


def _gap(b: list[float], v: list[float]) -> float:
    dx = max(v[0] - b[2], 0.0, b[0] - v[2])
    dy = max(v[1] - b[3], 0.0, b[1] - v[3])
    return math.hypot(dx, dy)


_LABEL = re.compile(r"^\s*(scale|sc\.?)\s*[:=]?\s*1\s*[:/]", re.I)


def _is_scale_label(c: dict) -> bool:
    """A printed scale that belongs to a drawing: it says "Scale", or names a paper size ("1:50 @ A1"). A bare "1:20
    gradient" or "Detail 1:5" is not a scale."""
    return bool(c.get("paper")) or bool(_LABEL.match(c.get("text", "")))


def assign_scales(scene: dict, items: list[dict]) -> dict[str, Any]:
    """Decide a ratio for each drawing without a scale.

    A scale written next to a drawing ("Scale 1:1250" under a location plan) belongs to that drawing; the title block's
    "1:200 @ A1" covers the others. Nothing is applied unless a scale naming the paper size matches the page.
    Returns {"found": best candidate or None, "ratios": {view_id: ratio}, "labelled": [...]}.
    """
    suggestion = ocr.parse_scale(items, scene["width"], scene["height"])
    cands = ocr.scale_candidates(items)
    drawings = [v for v in scene["views"] if v.get("kind") == "drawing"]
    title = [v for v in scene["views"] if v.get("kind") == "titleblock"]
    ratios: dict[str, int] = {}
    labelled: list[dict] = []
    if not suggestion or suggestion.get("page_matches_paper") is not True:
        return {"found": suggestion, "ratios": ratios, "labelled": labelled}
    for v in drawings:
        if v.get("mm_per_pt"):
            continue
        near = [(c, _gap(c["bbox"], v["bbox"])) for c in cands
                if _is_scale_label(c) and not any(_gap(c["bbox"], t["bbox"]) < 1 for t in title)]
        near = [x for x in near if x[1] <= NEAR_PT]
        if near:
            c = min(near, key=lambda x: x[1])[0]
            ratios[v["id"]] = c["ratio"]
            labelled.append({"view": v["id"], "text": c["text"], "ratio": c["ratio"]})
        else:
            ratios[v["id"]] = suggestion["ratio"]
    return {"found": suggestion, "ratios": ratios, "labelled": labelled}


def view_scale_fn(scene: dict):
    """(x, y) -> millimetres per point of the drawing nearest that point, or None when it has no scale."""
    views = [v for v in scene["views"] if v.get("kind") == "drawing"]

    def at(x: float, y: float) -> float | None:
        best, bd = None, math.inf
        for v in views:
            x0, y0, x1, y1 = v["bbox"]
            d = math.hypot(max(x0 - x, 0, x - x1), max(y0 - y, 0, y - y1))
            if d < bd:
                best, bd = v, d
        return best.get("mm_per_pt") if best else None
    return at


def run(job_dir: Path, page: int, scene: dict, apply_scale) -> dict[str, Any]:
    """Detect rooms on a page. `apply_scale({view_id: ratio})` stores the ratios read from the drawing."""
    try:
        items, info = text_items(job_dir, page, scene)
    except Exception as e:
        raise TextReadError(f"Reading the drawing text failed ({type(e).__name__}); try again.") from e
    plan = assign_scales(scene, items)
    suggestion = plan["found"]
    scale_applied = None
    drawings = [v for v in scene["views"] if v.get("kind") == "drawing"]
    if plan["ratios"]:
        apply_scale(plan["ratios"])
        for v in scene["views"]:
            if v["id"] in plan["ratios"]:
                v["ratio"] = plan["ratios"][v["id"]]
                v["mm_per_pt"] = PT_TO_MM * v["ratio"]
        scale_applied = suggestion
    at = view_scale_fn(scene)
    if not any(at((v["bbox"][0] + v["bbox"][2]) / 2, (v["bbox"][1] + v["bbox"][3]) / 2) for v in drawings):
        return {"error": "scale", "scale_suggestion": suggestion, "ocr": info}
    res = rooms.detect_rooms(scene, at, ocr.room_labels(items), texts=items)
    for r in res["rooms"]:
        r["default_selected"] = bool(r.get("plan_confirmed", True) and (r["named"] or r["area_m2"] >= 3.0))
    notes = list(res["notes"])
    if not info["available"]:
        notes.append("Room names could not be read: install the OCR add-on (pip install -r backend/requirements-ocr.txt) and try again.")
    return {"rooms": res["rooms"], "notes": notes, "scale_suggestion": suggestion, "scale_applied": scale_applied, "ocr": info}
