"""Automatic room detection from the extracted plan geometry.

1. Walls (heavy lines), hatched wall bands and medium-weight lines (windows, fixtures) form a barrier.
2. Door swings are quarter-circle arcs: the hinge-to-jamb line across each doorway closes it.
3. The free space inside the barrier splits into closed regions: candidate rooms.
4. Where an open-plan area or an archway joins several rooms, each room *name* found by OCR acts as a seed
   that grows through the free space until it meets its neighbour (a geodesic split), and the result is flagged
   as an estimated boundary.
All coordinates are PDF points (y down).
"""
import math
import re
from collections import deque
from typing import Any

import numpy as np
import shapely
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box
from shapely.ops import unary_union
from shapely.strtree import STRtree

MIN_ROOM_M2 = 1.2
MAX_ROOM_M2 = 400.0
MIN_WIDTH_MM = 450.0
DOOR_MM = (550.0, 1300.0)        # door leaf widths that count as a doorway closer
CELL_PT = 3.0                    # raster cell for geodesic splitting
LABEL_SNAP_PT = 40.0
DASH_MAX_PT = 14.0               # short, unconnected strokes are dashed roof/ceiling lines, not barriers


def _segments(flat: list[float], bbox: tuple[float, float, float, float] | None) -> list[LineString]:
    out = []
    for i in range(0, len(flat), 4):
        x0, y0, x1, y1 = flat[i:i + 4]
        if bbox and not (bbox[0] <= max(x0, x1) and min(x0, x1) <= bbox[2] and bbox[1] <= max(y0, y1) and min(y0, y1) <= bbox[3]):
            continue
        out.append(LineString([(x0, y0), (x1, y1)]))
    return out


def _drop_dashes(lines: list[LineString], solid) -> list[LineString]:
    """Remove short strokes whose ends touch nothing: CAD exports draw dashed lines as separate dashes."""
    if not lines:
        return lines
    tree = STRtree(lines)
    keep = []
    for i, ln in enumerate(lines):
        if ln.length >= DASH_MAX_PT:
            keep.append(ln)
            continue
        ok = True
        for x, y in (ln.coords[0], ln.coords[-1]):
            p = Point(x, y).buffer(0.8)
            touches = any(j != i for j in tree.query(p, predicate="intersects")) or solid.intersects(p)
            if not touches:
                ok = False
                break
        if ok:
            keep.append(ln)
    return keep


def _mass_boxes(mass: dict, bbox) -> list[Polygon]:
    c = mass.get("cell", 8)
    cells = mass.get("cells", [])
    out = []
    for i in range(0, len(cells), 2):
        x0, y0 = cells[i] * c, cells[i + 1] * c
        if bbox and not (bbox[0] <= x0 + c and x0 <= bbox[2] and bbox[1] <= y0 + c and y0 <= bbox[3]):
            continue
        out.append(box(x0, y0, x0 + c, y0 + c))
    return out


def _wall_fraction(poly: Polygon, solid) -> float:
    ring = poly.exterior
    if ring.length == 0:
        return 0.0
    return min(1.0, ring.intersection(solid.buffer(1.5)).length / ring.length)


def _largest(g) -> Polygon | None:
    if g.is_empty:
        return None
    if isinstance(g, Polygon):
        return g
    polys = [p for p in getattr(g, "geoms", []) if isinstance(p, Polygon)]
    return max(polys, key=lambda p: p.area) if polys else None


def _split_by_seeds(comp: Polygon, seeds: list[dict], solid) -> list[dict]:
    """Partition one free region between several named seeds by simultaneous flood fill on a grid."""
    minx, miny, maxx, maxy = comp.bounds
    nx, ny = int((maxx - minx) / CELL_PT) + 2, int((maxy - miny) / CELL_PT) + 2
    xs = minx + (np.arange(nx) + 0.5) * CELL_PT
    ys = miny + (np.arange(ny) + 0.5) * CELL_PT
    X, Y = np.meshgrid(xs, ys)
    core = comp.buffer(-0.75 * CELL_PT)                  # thin walls become thick enough that no 4-step can cross them
    free = shapely.contains_xy(core if not core.is_empty else comp, X, Y)
    label = np.full((ny, nx), -1, dtype=np.int16)
    q: deque[tuple[int, int]] = deque()
    fy, fx = np.nonzero(free)
    if len(fx) == 0:
        return []
    for k, sd in enumerate(seeds):
        d = (xs[fx] - sd["x"]) ** 2 + (ys[fy] - sd["y"]) ** 2
        j = int(np.argmin(d))
        cy, cx = int(fy[j]), int(fx[j])
        if label[cy, cx] == -1:
            label[cy, cx] = k
            q.append((cy, cx))
    while q:                                             # multi-source BFS: regions meet where growth meets
        cy, cx = q.popleft()
        k = label[cy, cx]
        for ny_, nx_ in ((cy + 1, cx), (cy - 1, cx), (cy, cx + 1), (cy, cx - 1)):
            if 0 <= ny_ < ny and 0 <= nx_ < nx and free[ny_, nx_] and label[ny_, nx_] == -1:
                label[ny_, nx_] = k
                q.append((ny_, nx_))
    cells = []
    for k in range(len(seeds)):
        yy, xx = np.nonzero(label == k)
        if len(xx) == 0:
            cells.append(Polygon())
            continue
        h = CELL_PT / 2
        cells.append(unary_union(shapely.box(xs[xx] - h, ys[yy] - h, xs[xx] + h, ys[yy] + h)))
    pieces = []
    for k, sd in enumerate(seeds):
        if cells[k].is_empty:
            continue
        piece = _largest(comp.intersection(cells[k].buffer(CELL_PT * 1.1)))
        if piece is not None:
            pieces.append({"seed": sd, "poly": piece, "cells": cells[k]})
    done: list[Polygon] = []
    for p in sorted(pieces, key=lambda p: p["poly"].area):          # remove the thin overlap band, small rooms first
        g = p["poly"]
        for prev in done:
            g = g.difference(prev)
        g = _largest(g.buffer(0))
        if g is not None:
            p["poly"] = g
            done.append(g)
    return [p for p in pieces if p["poly"] is not None]


def view_hint(texts: list[dict], bbox) -> str | None:
    """What the drawing's own title says it is: 'plan', 'elevation' or 'section' (titles sit just below or above a view)."""
    near = [t for t in texts if bbox[0] - 40 <= (t["bbox"][0] + t["bbox"][2]) / 2 <= bbox[2] + 40
            and bbox[1] - 160 <= (t["bbox"][1] + t["bbox"][3]) / 2 <= bbox[3] + 160]
    words = " ".join(t["text"].lower() for t in near)
    if "elevation" in words:
        return "elevation"
    if "section" in words:
        return "section"
    if re.search(r"\bplan\b", words):
        return "plan"
    return None


def detect_rooms(scene: dict, s, labels: list[dict] | None = None,
                 view_bboxes: list[tuple[float, float, float, float]] | None = None,
                 texts: list[dict] | None = None) -> dict[str, Any]:
    """Find candidate rooms. `s` is millimetres per point: a number, or a function (x, y) -> number | None
    so each drawing can have its own scale. Returns {rooms: [...], notes: [...]}."""
    labels = labels or []
    texts = texts or []
    notes: list[str] = []
    views = view_bboxes if view_bboxes is not None else [tuple(v["bbox"]) for v in scene.get("views", []) if v.get("kind") == "drawing"]
    if not views:
        views = [(0.0, 0.0, scene["width"], scene["height"])]
    arcs = scene.get("arcs", [])
    rooms: list[dict] = []
    skipped: list[str] = []
    for bbox in views:
        box_ = (bbox[0] - 20, bbox[1] - 20, bbox[2] + 20, bbox[3] + 20)
        sv = s((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2) if callable(s) else s
        if not sv:
            continue
        vlabels = [lb for lb in labels if box_[0] <= lb["x"] <= box_[2] and box_[1] <= lb["y"] <= box_[3]]
        doors = [arcs[i:i + 7] for i in range(0, len(arcs), 7)
                 if box_[0] <= arcs[i + 4] <= box_[2] and box_[1] <= arcs[i + 5] <= box_[3] and DOOR_MM[0] <= arcs[i + 6] * sv <= DOOR_MM[1]]
        hint = view_hint(texts, bbox)
        if hint in ("elevation", "section"):
            skipped.append(hint)
            continue
        if hint != "plan" and len(doors) < 3 and not vlabels:
            continue                                   # no sign of a floor plan here
        got = _detect_view(scene, sv, vlabels, doors, box_)
        rooms += got
    if skipped:
        notes.append(f"Skipped {len(skipped)} drawing(s) titled as elevations or sections.")
    if not rooms:
        notes.append("No enclosed rooms were found. Draw them by hand, or check that the scale is set.")
    for i, r in enumerate(rooms):
        r["id"] = f"auto{i + 1}"
    return {"rooms": rooms, "notes": notes}


def _detect_view(scene: dict, s: float, labels: list[dict], doors: list[list[float]], bbox) -> list[dict]:
    walls = _segments(scene.get("walls", []), bbox)
    details = _segments(scene.get("detail", []), bbox)
    boxes = _mass_boxes(scene.get("mass", {}), bbox)
    solid = unary_union([w.buffer(0.4) for w in walls] + boxes) if (walls or boxes) else Polygon()
    details = _drop_dashes(details, solid)

    closers = []
    for x0, y0, x1, y1, hx, hy, _r in doors:           # the closed leaf lies along the wall: its tip touches the jamb
        d0, d1 = solid.distance(Point(x0, y0)), solid.distance(Point(x1, y1))
        tip = (x0, y0) if d0 <= d1 else (x1, y1)
        closers.append(LineString([(hx, hy), tip]))
    barrier = unary_union([solid] + [d.buffer(0.4) for d in details] + [c.buffer(0.8) for c in closers])
    if barrier.is_empty:
        return []
    frame = box(*bbox)
    free = frame.difference(barrier)
    comps = [g for g in (free.geoms if isinstance(free, MultiPolygon) else [free]) if isinstance(g, Polygon)]
    out_boundary = frame.exterior.buffer(1.0)

    cands = []
    for g in comps:
        if g.intersects(out_boundary):
            continue                                   # open to the outside
        area = g.area * s * s / 1e6
        if not (MIN_ROOM_M2 <= area <= MAX_ROOM_M2):
            continue
        ext = Polygon(g.exterior)
        if ext.buffer(-MIN_WIDTH_MM / 2 / s).is_empty:
            continue                                   # too thin to be a room (wall cavities, gaps)
        cands.append({"free": g, "ext": ext, "area": area, "wall_frac": _wall_fraction(ext, solid)})

    # A region sitting inside another one is either furniture (outlined by light lines) or a room walled off inside a
    # larger space. One that holds a room name is a room; furniture footprints are given back to the neighbouring room.
    keep, islands = [], []
    for c in cands:
        inside_other = any(o is not c and o["ext"].contains(c["ext"]) for o in cands)
        named = any(c["free"].contains(Point(lb["x"], lb["y"])) for lb in labels)
        if inside_other and c["wall_frac"] < 0.6 and not named:
            islands.append(c["ext"])
            continue
        keep.append(c)

    # Each label belongs to the region it sits in; a label on a light line snaps to the nearest region.
    assigned: dict[int, list[dict]] = {i: [] for i in range(len(keep))}
    for lb in labels:
        pt = Point(lb["x"], lb["y"])
        home = next((i for i, c in enumerate(keep) if c["free"].contains(pt)), None)
        if home is None:
            near = [(c["free"].distance(pt), i) for i, c in enumerate(keep)]
            d, i = min(near) if near else (1e9, None)
            if i is not None and d <= LABEL_SNAP_PT:
                home = i
        if home is not None:
            assigned[home].append(lb)

    rooms = []
    for i, c in enumerate(keep):
        inside = assigned[i]
        if c["wall_frac"] < 0.3 and not inside:
            continue                                   # unlabelled and not bounded by walls: not a room
        nested = [o["ext"] for o in keep if o is not c and c["ext"].contains(o["ext"])]   # rooms walled off inside this one
        if len(inside) >= 2:
            pieces = [{"seed": p["seed"], "poly": Polygon(p["poly"].exterior)} for p in _split_by_seeds(c["free"], inside, solid)]
            for isl in (g for g in islands if c["ext"].contains(g)):      # furniture footprints go to the room they touch most
                grown = isl.buffer(2.5)                                  # bridges the footprint's own outline
                best = max(pieces, key=lambda p: p["poly"].intersection(grown).length, default=None)
                if best is not None and best["poly"].distance(isl) <= 3.0:
                    best["poly"] = _largest(unary_union([best["poly"], grown]).buffer(0)) or best["poly"]
            for p in pieces:
                rooms.append(_room(_cut(p["poly"], nested), s, p["seed"]["text"], solid, split=True))
        else:
            rooms.append(_room(_cut(c["ext"], nested), s, inside[0]["text"] if inside else None, solid, split=False))
    return [r for r in rooms if r]


def _cut(poly: Polygon, nested: list[Polygon]) -> Polygon:
    """Remove nested rooms from this one where they touch its edge. A room walled off in the middle would leave a hole,
    which a saved room (a single outline) cannot hold, so the outline stays whole in that case."""
    for n in nested:
        cut = _largest(poly.difference(n).buffer(0))
        if cut is not None and not list(cut.interiors):
            poly = cut
    return poly


def _room(poly: Polygon, s: float, name: str | None, solid, split: bool) -> dict | None:
    area = poly.area * s * s / 1e6
    if area < MIN_ROOM_M2:
        return None
    simple = poly.simplify(0.6)
    tol = 0.6
    while len(simple.exterior.coords) > 250 and tol < 10:
        tol *= 1.6
        simple = poly.simplify(tol)
    frac = _wall_fraction(simple, solid)
    return {
        "name": name,
        "poly": [[round(x, 1), round(y, 1)] for x, y in list(simple.exterior.coords)[:-1]],
        "area_m2": round(area, 2),
        "perimeter_m": round(poly.exterior.length * s / 1000, 2),
        "wall_fraction": round(frac, 2),
        "boundary": "estimated" if split and frac < 0.8 else "walls",
        "named": name is not None,
    }
