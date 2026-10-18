"""PDF -> structured scene extraction (vector PDFs via PyMuPDF).

A scene is plain JSON: classified paths, live text, snap points and "views"
(the separate drawings on a sheet: plan, each elevation, title block ...).
Coordinates are PDF points, origin top-left, y down.
"""
import re
from typing import Any

import pymupdf

MAX_PAGES = 30
PT_TO_MM = 25.4 / 72.0

# Path categories, used by the viewer for declutter toggles.
CATEGORIES = [
    ("structure", "Structure (heavy lines)"),
    ("detail", "Detail (medium lines)"),
    ("hatch", "Wall hatching"),
    ("fine", "Fine lines (dims, leaders, misc)"),
    ("dashed", "Dashed / hidden"),
    ("fills", "Solid fills"),
    ("text", "Text (outlined)"),
]

HATCH_MAX_LEN = 25.0       # hatch strokes are short straight segments...
HATCH_NEIGHBOURS = 6       # ...with at least this many siblings nearby
HATCH_RADIUS = 12.0
WORD_MAX_DIM = 80.0        # larger filled shapes are never text
GLYPH_MAX_DIM = 14.0       # filled shapes smaller than this (pt) are treated as text glyphs
HEAVY_W = 0.48             # stroke widths (pt) at/above which a line is "structure"
MEDIUM_W = 0.24
LONG_LINE = 500.0          # datum/level lines longer than this don't define a view
VIEW_TOL = 30.0            # gap (pt) below which heavy linework joins into one view
CORNER_MARGIN = 120.0
MAX_SNAPS = 60000
MAX_WALL_SEGS = 40000
MASS_CELL = 8.0            # hatched wall bands are exposed as occupied grid cells of this size (pt)


def _hex(c) -> str | None:
    if c is None:
        return None
    r, g, b = (max(0, min(255, round(v * 255))) for v in c[:3])
    return f"#{r:02x}{g:02x}{b:02x}"


def _f(v: float) -> str:
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _pt(p) -> str:
    return f"{_f(p.x)} {_f(p.y)}"


def _path_d(items, close: bool) -> str:
    """Build an SVG path string from PyMuPDF drawing items."""
    out: list[str] = []
    cur = None
    for it in items:
        k = it[0]
        if k == "l":
            a, b = it[1], it[2]
            if cur is None or abs(cur.x - a.x) > 0.01 or abs(cur.y - a.y) > 0.01:
                out.append(f"M{_pt(a)}")
            out.append(f"L{_pt(b)}")
            cur = b
        elif k == "c":
            a, c1, c2, b = it[1], it[2], it[3], it[4]
            if cur is None or abs(cur.x - a.x) > 0.01 or abs(cur.y - a.y) > 0.01:
                out.append(f"M{_pt(a)}")
            out.append(f"C{_pt(c1)} {_pt(c2)} {_pt(b)}")
            cur = b
        elif k == "re":
            r = it[1]
            out.append(f"M{_f(r.x0)} {_f(r.y0)}H{_f(r.x1)}V{_f(r.y1)}H{_f(r.x0)}Z")
            cur = None
        elif k == "qu":
            q = it[1]
            out.append(f"M{_pt(q.ul)}L{_pt(q.ur)}L{_pt(q.lr)}L{_pt(q.ll)}Z")
            cur = None
    if close and out:
        out.append("Z")
    return "".join(out)


def _dash(dashes: str | None) -> list[float] | None:
    if not dashes:
        return None
    m = re.match(r"\s*\[([^\]]*)\]", dashes)
    if not m:
        return None
    nums = [float(x) for x in m.group(1).split() if x]
    return nums if nums and any(n > 0 for n in nums) else None


def _grid_neighbours(centres: list[tuple[float, float]], radius: float) -> list[int]:
    """For each centre, how many *other* centres lie within `radius`."""
    cell = radius
    grid: dict[tuple[int, int], list[int]] = {}
    for i, (x, y) in enumerate(centres):
        grid.setdefault((int(x // cell), int(y // cell)), []).append(i)
    out = []
    r2 = radius * radius
    for i, (x, y) in enumerate(centres):
        cx, cy = int(x // cell), int(y // cell)
        n = 0
        for gx in (cx - 1, cx, cx + 1):
            for gy in (cy - 1, cy, cy + 1):
                for j in grid.get((gx, gy), ()):
                    if j != i and (centres[j][0] - x) ** 2 + (centres[j][1] - y) ** 2 <= r2:
                        n += 1
        out.append(n)
    return out


def _is_filled(d: dict) -> bool:
    return d["type"] in ("f", "fs")


def _classify_all(drawings: list[dict]) -> list[str]:
    """Assign a declutter category to every drawing.

    Outlined text and hatching are recognised by context (many similar neighbours),
    because CAD PDFs carry no layer or text information for them.
    """
    cats = [""] * len(drawings)

    # Text glyphs: tiny filled shapes, or mid-sized filled shapes that sit in a word.
    glyph_idx = [i for i, d in enumerate(drawings)
                 if _is_filled(d) and max(d["rect"].width, d["rect"].height) < WORD_MAX_DIM]
    centres = [((drawings[i]["rect"].x0 + drawings[i]["rect"].x1) / 2,
                (drawings[i]["rect"].y0 + drawings[i]["rect"].y1) / 2) for i in glyph_idx]
    near = _grid_neighbours(centres, 45.0)
    in_word = {i for i, n in zip(glyph_idx, near) if n >= 1}
    for i in glyph_idx:
        d = drawings[i]
        if max(d["rect"].width, d["rect"].height) < GLYPH_MAX_DIM or i in in_word:
            cats[i] = "text"

    # Hatching: short straight hairlines in dense groups.
    hatch_idx = []
    for i, d in enumerate(drawings):
        if cats[i] or d["type"] != "s" or (d.get("width") or 0) >= MEDIUM_W:
            continue
        r = d["rect"]
        if len(d["items"]) == 1 and d["items"][0][0] == "l" and max(r.width, r.height) <= HATCH_MAX_LEN:
            hatch_idx.append(i)
    hc = [((drawings[i]["rect"].x0 + drawings[i]["rect"].x1) / 2,
           (drawings[i]["rect"].y0 + drawings[i]["rect"].y1) / 2) for i in hatch_idx]
    for i, n in zip(hatch_idx, _grid_neighbours(hc, HATCH_RADIUS)):
        if n >= HATCH_NEIGHBOURS:
            cats[i] = "hatch"

    for i, d in enumerate(drawings):
        if cats[i]:
            continue
        if _is_filled(d):
            cats[i] = "fills" if d["type"] == "f" else "detail"
        elif _dash(d.get("dashes")):
            cats[i] = "dashed"
        else:
            w = d.get("width") or 0
            cats[i] = "structure" if w >= HEAVY_W else "detail" if w >= MEDIUM_W else "fine"
    return cats


def _segments(d: dict):
    """Straight edges of a path (curves are approximated by their chord)."""
    for it in d["items"]:
        k = it[0]
        if k == "l":
            yield it[1], it[2]
        elif k == "c":
            yield it[1], it[4]
        elif k == "re":
            r = it[1]
            a, b = pymupdf.Point(r.x0, r.y0), pymupdf.Point(r.x1, r.y0)
            c, e = pymupdf.Point(r.x1, r.y1), pymupdf.Point(r.x0, r.y1)
            yield a, b
            yield b, c
            yield c, e
            yield e, a
        elif k == "qu":
            q = it[1]
            yield q.ul, q.ur
            yield q.ur, q.lr
            yield q.lr, q.ll
            yield q.ll, q.ul


def _snap_points(d: dict):
    for it in d["items"]:
        k = it[0]
        if k == "l":
            yield it[1]
            yield it[2]
        elif k == "c":
            yield it[1]
            yield it[4]
        elif k == "re":
            r = it[1]
            yield pymupdf.Point(r.x0, r.y0)
            yield pymupdf.Point(r.x1, r.y0)
            yield pymupdf.Point(r.x1, r.y1)
            yield pymupdf.Point(r.x0, r.y1)
        elif k == "qu":
            q = it[1]
            yield q.ul
            yield q.ur
            yield q.lr
            yield q.ll


def _find_views(page: pymupdf.Page, drawings: list[dict]) -> list[dict]:
    """Split the sheet into separate drawings by clustering heavy linework."""
    heavy = [
        d for d in drawings
        if d["type"] == "s" and (d.get("width") or 0) >= HEAVY_W
        and max(d["rect"].width, d["rect"].height) < LONG_LINE
    ]
    if not heavy:
        return []
    clusters = [c for c in page.cluster_drawings(drawings=heavy, x_tolerance=VIEW_TOL, y_tolerance=VIEW_TOL)
                if c.width * c.height > 6000]

    pw, ph = page.rect.width, page.rect.height
    corner = pymupdf.Rect(pw - CORNER_MARGIN, ph - CORNER_MARGIN, pw, ph)

    views: list[dict] = []
    for c in clusters:
        if not _overlaps(c, corner):
            views.append({"bbox": c, "kind": "drawing"})
            continue
        # A drawing may have merged with the title block: re-cluster tighter.
        inside = [d for d in heavy if _overlaps(d["rect"], c)]
        subs = [s for s in page.cluster_drawings(drawings=inside, x_tolerance=8, y_tolerance=8)
                if s.width * s.height > 6000]
        tb = [s for s in subs if _overlaps(s, corner) and s.width < pw * 0.4 and s.height < ph * 0.5]
        if not tb:
            views.append({"bbox": c, "kind": "drawing"})
            continue
        tb_rect = pymupdf.Rect(tb[0])
        for s in tb[1:]:
            tb_rect |= s
        for img in page.get_image_info():
            ib = pymupdf.Rect(img["bbox"])
            if _overlaps(ib, tb_rect):
                tb_rect |= ib
        views.append({"bbox": tb_rect, "kind": "titleblock"})
        # Boxes stacked directly above/beside the title block (logo, RIBA stamp) belong to it.
        for _ in range(2):
            for s in subs:
                aligned = s.x0 >= tb_rect.x0 - 20 and s.x1 <= tb_rect.x1 + 20
                if aligned and s.y1 >= tb_rect.y0 - 60:
                    tb_rect |= s
                    continue
                # A sparse box (logo / stamp) tucked into the bottom-right corner region.
                members = sum(1 for d in inside if _overlaps(d["rect"], s))
                if members <= 8 and s.x0 > pw * 0.6 and s.y0 > ph * 0.6 and s.width < pw * 0.25:
                    tb_rect |= s
        rest = [s for s in subs if not _overlaps(s, tb_rect)]
        if rest:
            rect = pymupdf.Rect(rest[0])
            for s in rest[1:]:
                rect |= s
            views.append({"bbox": rect, "kind": "drawing"})

    views.sort(key=lambda v: (v["kind"] == "titleblock", round(v["bbox"].y0 / 200), v["bbox"].x0))
    n = 0
    for i, v in enumerate(views):
        v["id"] = f"v{i + 1}"
        if v["kind"] == "titleblock":
            v["name"] = "Title block"
        else:
            n += 1
            v["name"] = f"Drawing {n}"
        r = v["bbox"]
        v["bbox"] = [round(r.x0, 1), round(r.y0, 1), round(r.x1, 1), round(r.y1, 1)]
    return views


def _overlaps(a, b) -> bool:
    """Inclusive rect overlap (Rect.intersects is False for zero-width/height lines)."""
    return a.x0 <= b.x1 and b.x0 <= a.x1 and a.y0 <= b.y1 and b.y0 <= a.y1


def _assign_view(cx: float, cy: float, views: list[dict]) -> str | None:
    best, best_d = None, 1e18
    for v in views:
        x0, y0, x1, y1 = v["bbox"]
        dx = max(x0 - cx, 0, cx - x1)
        dy = max(y0 - cy, 0, cy - y1)
        dist = dx * dx + dy * dy
        if dist < best_d:
            best, best_d = v["id"], dist
    return best


def page_kind(n_paths: int, n_chars: int, page: pymupdf.Page) -> str:
    """Rough vector-vs-scan classification."""
    if n_paths < 50 and n_chars == 0:
        for img in page.get_image_info():
            if pymupdf.Rect(img["bbox"]).get_area() > 0.6 * page.rect.get_area():
                return "scan"
    return "vector"


def extract_page(page: pymupdf.Page) -> dict[str, Any]:
    # get_drawings() reports unrotated coordinates, so normalise the page first;
    # that keeps paths, text, snaps and views in one frame (the sheet's native orientation).
    if page.rotation:
        page.set_rotation(0)
    drawings = page.get_drawings()

    texts = []
    n_chars = 0
    for b in page.get_text("dict")["blocks"]:
        if b["type"] != 0:
            continue
        for ln in b["lines"]:
            txt = "".join(s["text"] for s in ln["spans"]).strip()
            if not txt:
                continue
            n_chars += len(txt)
            x0, y0, x1, y1 = ln["bbox"]
            texts.append({
                "text": txt, "x": round(x0, 1), "y": round(y1 - (y1 - y0) * 0.2, 1),
                "size": round(ln["spans"][0]["size"], 1),
                "bbox": [round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1)],
            })

    kind = page_kind(len(drawings), n_chars, page)
    views = _find_views(page, drawings) if kind == "vector" else []

    paths = []
    snaps: dict[tuple[int, int], tuple[float, float]] = {}
    walls: list[float] = []
    mass: set[tuple[int, int]] = set()
    counts = {k: 0 for k, _ in CATEGORIES}
    cats = _classify_all(drawings)
    for d, cat in zip(drawings, cats):
        r = d["rect"]
        if not r.is_valid or r.is_empty and not d["items"]:
            continue
        counts[cat] += 1
        dstr = _path_d(d["items"], bool(d.get("closePath")))
        if not dstr:
            continue
        p: dict[str, Any] = {
            "d": dstr, "cat": cat,
            "v": _assign_view((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2, views) if views else None,
        }
        w = d.get("width")
        if d["type"] in ("s", "fs") and w is not None:
            p["w"] = round(w, 2)
            p["s"] = _hex(d.get("color")) or "#000000"
        if d["type"] in ("f", "fs"):
            p["f"] = _hex(d.get("fill")) or "#000000"
            if d.get("even_odd"):
                p["eo"] = 1
        dash = _dash(d.get("dashes"))
        if dash:
            p["da"] = dash
        paths.append(p)
        if cat == "hatch" and len(mass) < 60000:
            for cx in range(int(r.x0 // MASS_CELL), int(r.x1 // MASS_CELL) + 1):
                for cy in range(int(r.y0 // MASS_CELL), int(r.y1 // MASS_CELL) + 1):
                    mass.add((cx, cy))
        if cat == "structure" and len(walls) < MAX_WALL_SEGS * 4:
            for a, b in _segments(d):
                if abs(a.x - b.x) + abs(a.y - b.y) > 0.5:
                    walls += [round(a.x, 1), round(a.y, 1), round(b.x, 1), round(b.y, 1)]
        if cat in ("structure", "detail") and len(snaps) < MAX_SNAPS:
            for pt in _snap_points(d):
                snaps[(round(pt.x * 2), round(pt.y * 2))] = (round(pt.x, 1), round(pt.y, 1))

    return {
        "width": round(page.rect.width, 2),
        "height": round(page.rect.height, 2),
        "rotation": page.rotation,
        "kind": kind,
        "views": views,
        "paths": paths,
        "texts": texts,
        "snaps": [c for pt in snaps.values() for c in pt],
        "walls": walls,
        "mass": {"cell": MASS_CELL, "cells": [c for xy in sorted(mass) for c in xy]},
        "counts": counts,
        "n_chars": n_chars,
    }


def extract_pdf(pdf_path: str) -> list[dict[str, Any]]:
    doc = pymupdf.open(pdf_path)
    try:
        if doc.needs_pass:
            raise ValueError("PDF is password protected")
        if doc.page_count > MAX_PAGES:
            raise ValueError(f"PDF has {doc.page_count} pages; the limit is {MAX_PAGES}")
        return [extract_page(p) for p in doc]
    finally:
        doc.close()
