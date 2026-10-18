"""Orchestration for automatic room detection: read the text once (cached), find the scale, detect rooms."""
import json
import math
from pathlib import Path
from typing import Any

import ocr
import rooms

PT_TO_MM = 25.4 / 72.0
PAD = 90.0


def text_items(job_dir: Path, page: int, scene: dict) -> tuple[list[dict], dict[str, Any]]:
    """Live text plus OCR of the outlined text, cached per page. Degrades to live text when OCR isn't installed."""
    info: dict[str, Any] = {"available": ocr.available(), "ran": False, "cached": False}
    cache = job_dir / f"ocr-{page}.json"
    items: list[dict] = []
    if cache.exists():
        items = json.loads(cache.read_text())
        info["cached"] = True
    elif info["available"]:
        w, h = scene["width"], scene["height"]
        regions = [(max(0.0, v["bbox"][0] - PAD), max(0.0, v["bbox"][1] - PAD), min(w, v["bbox"][2] + PAD), min(h, v["bbox"][3] + PAD))
                   for v in scene["views"]] or [(0.0, 0.0, w, h)]
        items = ocr.ocr_page(str(job_dir / "original.pdf"), page, regions)
        cache.write_text(json.dumps(items))
        info["ran"] = True
    live = [{"text": t["text"], "conf": 1.0, "bbox": t["bbox"]} for t in scene.get("texts", [])]
    merged = ocr._dedupe(items + live)
    info["items"] = len(merged)
    return merged, info


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
    """Detect rooms on a page. `apply_scale(ratio)` stores a ratio on every drawing that has no scale yet."""
    items, info = text_items(job_dir, page, scene)
    suggestion = ocr.parse_scale(items, scene["width"], scene["height"])
    scale_applied = None
    drawings = [v for v in scene["views"] if v.get("kind") == "drawing"]
    if drawings and any(not v.get("mm_per_pt") for v in drawings):
        # Only trust a printed scale when it names a paper size and the page really is that size ("1:50 @ A1").
        if suggestion and suggestion.get("page_matches_paper") is True:
            apply_scale(suggestion["ratio"])
            for v in drawings:
                if not v.get("mm_per_pt"):
                    v["mm_per_pt"] = PT_TO_MM * suggestion["ratio"]
                    v["ratio"] = suggestion["ratio"]
            scale_applied = suggestion
    at = view_scale_fn(scene)
    if not any(at((v["bbox"][0] + v["bbox"][2]) / 2, (v["bbox"][1] + v["bbox"][3]) / 2) for v in drawings):
        return {"error": "scale", "scale_suggestion": suggestion, "ocr": info}
    res = rooms.detect_rooms(scene, at, ocr.room_labels(items), texts=items)
    for r in res["rooms"]:
        r["default_selected"] = bool(r["named"] or r["area_m2"] >= 3.0)
    notes = list(res["notes"])
    if not info["available"]:
        notes.append("Room names could not be read: install the OCR add-on (pip install -r backend/requirements-ocr.txt) and try again.")
    return {"rooms": res["rooms"], "notes": notes, "scale_suggestion": suggestion, "scale_applied": scale_applied, "ocr": info}
