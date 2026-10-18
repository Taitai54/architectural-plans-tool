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
BRIDGE_MAX_MM = 2500.0          # aligned wall ends facing each other closer than this are an opening (a window or doorway gap)
BRIDGE_MIN_MM = 450.0
BRIDGE_SEG_MM = 600.0            # only ends of wall segments at least this long count (dashes of a site boundary do not)
FURNITURE_M2 = 6.0               # an unnamed region this small and not bounded by real walls is furniture, not a room
FURNITURE_WALL_FRAC = 0.7
GROUP_GAP_PT = 400.0            # drawings at the same scale this close together are parts of one plan (a wide opening splits the linework)
DASH_MAX_PT = 14.0               # short, unconnected strokes are dashed roof/ceiling lines, not barriers


def _segments(flat: list[float], bbox: tuple[float, float, float, float] | None) -> list[LineString]:
    out = []
    for i in range(0, len(flat), 4):
        x0, y0, x1, y1 = flat[i:i + 4]
        if not all(math.isfinite(v) for v in (x0, y0, x1, y1)):
            continue
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


def _solid_polys(rings: list[list[float]], bbox) -> list[Polygon]:
    out = []
    for flat in rings:
        pts = [(flat[i], flat[i + 1]) for i in range(0, len(flat) - 1, 2)]
        if len(pts) < 3:
            continue
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        if bbox and not (bbox[0] <= max(xs) and min(xs) <= bbox[2] and bbox[1] <= max(ys) and min(ys) <= bbox[3]):
            continue
        g = Polygon(pts)
        g = g if g.is_valid else g.buffer(0)
        if not g.is_empty:
            out.append(g)
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


def _bbox_gap(a, b) -> float:
    return math.hypot(max(a[0] - b[2], b[0] - a[2], 0), max(a[1] - b[3], b[1] - a[3], 0))


def _union(a, b):
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


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
    todo: list[dict] = []                                # [{bbox, s}] after dropping elevations/sections and merging fragments
    for bbox in views:
        sv = s((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2) if callable(s) else s
        if not sv:
            continue
        if view_hint(texts, bbox) in ("elevation", "section"):
            skipped.append(view_hint(texts, bbox))
            continue
        near = [g for g in todo if abs(g["s"] - sv) < 1e-6 and _bbox_gap(g["bbox"], bbox) <= GROUP_GAP_PT]
        if near:
            first = near[0]
            for other in near[1:]:
                first["bbox"] = _union(first["bbox"], other["bbox"])
                todo.remove(other)
            first["bbox"] = _union(first["bbox"], bbox)
        else:
            todo.append({"bbox": tuple(bbox), "s": sv})
    for grp in todo:
        bbox, sv = grp["bbox"], grp["s"]
        box_ = (bbox[0] - 20, bbox[1] - 20, bbox[2] + 20, bbox[3] + 20)
        vlabels = [lb for lb in labels if box_[0] <= lb["x"] <= box_[2] and box_[1] <= lb["y"] <= box_[3]]
        doors = [arcs[i:i + 7] for i in range(0, len(arcs), 7)
                 if box_[0] <= arcs[i + 4] <= box_[2] and box_[1] <= arcs[i + 5] <= box_[3] and DOOR_MM[0] <= arcs[i + 6] * sv <= DOOR_MM[1]]
        hint = view_hint(texts, bbox)
        confirmed = hint == "plan" or bool(vlabels) or len(doors) >= 3      # evidence that this really is a floor plan
        got = _detect_view(scene, sv, vlabels, doors, box_)
        for g in got:
            g["plan_confirmed"] = confirmed
        rooms += got
    if skipped:
        notes.append(f"Skipped {len(skipped)} drawing(s) titled as elevations or sections.")
    if rooms and not all(x["plan_confirmed"] for x in rooms):
        notes.append("Could not confirm this drawing is a floor plan (no plan title, room names or door swings found): check the result.")
    if not rooms:
        notes.append("No enclosed rooms were found. Draw them by hand, or check that the scale is set.")
    for i, r in enumerate(rooms):
        r["id"] = f"auto{i + 1}"
    return {"rooms": rooms, "notes": notes}


def _door_geometry(doors: list[list[float]], solid) -> tuple[list[LineString], list[tuple[Polygon, tuple[float, float]]]]:
    """For each door swing: the line(s) closing the doorway, and the swept sector with its open tip (the room it swings into)."""
    closers: list[LineString] = []
    sectors: list[tuple[Polygon, tuple[float, float]]] = []
    for x0, y0, x1, y1, hx, hy, rad in doors:
        d0, d1 = solid.distance(Point(x0, y0)), solid.distance(Point(x1, y1))
        near = min(d0, d1)
        # The closed leaf lies along the wall, so its tip touches the jamb. For double doors both tips sit mid-gap: close both.
        tips = [(x0, y0)] if (near <= 3.0 and d0 <= d1) else [(x1, y1)] if near <= 3.0 else [(x0, y0), (x1, y1)]
        closers += [LineString([(hx, hy), t]) for t in tips]
        a0, a1 = math.atan2(y0 - hy, x0 - hx), math.atan2(y1 - hy, x1 - hx)
        da = (a1 - a0 + math.pi) % (2 * math.pi) - math.pi
        pts = [(hx, hy)] + [(hx + rad * math.cos(a0 + da * i / 12), hy + rad * math.sin(a0 + da * i / 12)) for i in range(13)]
        sec = Polygon(pts)
        if sec.is_valid and not sec.is_empty:
            open_tip = (x0, y0) if d0 > d1 else (x1, y1)           # the tip away from the wall is in the room the door opens into
            sectors.append((sec, open_tip))
    return closers, sectors


def _bridge_gaps(walls: list[LineString], fixed, s: float) -> list[LineString]:
    """Join wall ends that face each other across a short gap: a window or doorway drawn as a bare opening.

    A wall end counts when the segment is long enough, or when its other end is joined to the rest of the wall (a short
    jamb stub). A dash of a dashed line has both ends loose, so it never counts.
    """
    max_d, min_d, cos_tol = BRIDGE_MAX_MM / s, BRIDGE_MIN_MM / s, math.cos(math.radians(12))
    tree = STRtree(walls) if walls else None

    def dangling(p: tuple[float, float], i: int) -> bool:
        probe = Point(p).buffer(1.2)
        if any(j != i for j in tree.query(probe, predicate="intersects")):
            return False
        return fixed is None or fixed.is_empty or not fixed.intersects(probe)

    free_ends = []
    for i, w in enumerate(walls):
        if w.length < 2:
            continue
        (ax, ay), (bx, by) = w.coords[0], w.coords[-1]
        ux, uy = (bx - ax) / w.length, (by - ay) / w.length
        a_free, b_free = dangling((ax, ay), i), dangling((bx, by), i)
        long_enough = w.length * s >= BRIDGE_SEG_MM
        if a_free and (long_enough or not b_free):
            free_ends.append(((ax, ay), (-ux, -uy), i))
        if b_free and (long_enough or not a_free):
            free_ends.append(((bx, by), (ux, uy), i))
    if len(free_ends) > 2500:
        return []
    bridges = []
    for p, u, i in free_ends:
        best = None
        for q, w_, j in free_ends:
            if j == i:
                continue
            vx, vy = q[0] - p[0], q[1] - p[1]
            d = math.hypot(vx, vy)
            if d < min_d or d > max_d:
                continue
            vx, vy = vx / d, vy / d
            if u[0] * vx + u[1] * vy < cos_tol or -(w_[0] * vx + w_[1] * vy) < cos_tol:
                continue                               # the two ends must point at each other
            if best is None or d < best[0]:
                best = (d, q)
        if best:
            bridges.append(LineString([p, best[1]]))
    return bridges


def _detect_view(scene: dict, s: float, labels: list[dict], doors: list[list[float]], bbox) -> list[dict]:
    walls = _segments(scene.get("walls", []), bbox)
    details = _segments(scene.get("detail", []), bbox)
    boxes = _mass_boxes(scene.get("mass", {}), bbox)
    poche = _solid_polys(scene.get("solids", []), bbox)          # walls drawn as solid black shapes
    mass_like = unary_union(boxes + poche) if (boxes or poche) else None
    solid = unary_union([w.buffer(0.4) for w in walls] + boxes + poche) if (walls or boxes or poche) else Polygon()
    details = _drop_dashes(details, solid)

    closers, sectors = _door_geometry(doors, solid)
    bridges = _bridge_gaps(walls, mass_like, s)
    barrier = unary_union([solid] + [d.buffer(0.4) for d in details] + [c.buffer(0.8) for c in closers] + [b.buffer(0.8) for b in bridges])
    if barrier.is_empty:
        return []
    frame = box(*bbox)
    free = frame.difference(barrier)
    comps = [g for g in (free.geoms if isinstance(free, MultiPolygon) else [free]) if isinstance(g, Polygon)]
    out_boundary = frame.exterior.buffer(1.0)

    outside = [g for g in comps if g.intersects(out_boundary)]
    # A room name sitting in a region that leaks outside (a bare window gap): close the gaps in the walls and look again.
    unresolved = [lb for lb in labels if any(g.contains(Point(lb["x"], lb["y"])) for g in outside)]
    comps = [g for g in comps if not g.intersects(out_boundary)]
    if unresolved and not solid.is_empty:
        rad = BRIDGE_MAX_MM / 2 / s
        walls_closed = unary_union([solid] + [c.buffer(0.8) for c in closers]).buffer(rad).buffer(-rad)
        barrier2 = unary_union([walls_closed] + [d.buffer(0.4) for d in details] + [b.buffer(0.8) for b in bridges])
        free2 = frame.difference(barrier2)
        for g in (free2.geoms if isinstance(free2, MultiPolygon) else [free2]):
            if isinstance(g, Polygon) and not g.intersects(out_boundary) and any(g.contains(Point(lb["x"], lb["y"])) for lb in unresolved):
                comps.append(g)

    cands = []
    for g in comps:
        area = g.area * s * s / 1e6
        if not (MIN_ROOM_M2 <= area <= MAX_ROOM_M2):
            continue
        ext = Polygon(g.exterior)
        if ext.buffer(-MIN_WIDTH_MM / 2 / s).is_empty:
            continue                                   # too thin to be a room (wall cavities, gaps)
        cands.append({"free": g, "ext": ext, "area": area, "wall_frac": _wall_fraction(ext, solid)})

    # Regions that are not rooms are handed back to the room they touch: furniture outlined by light lines (islands inside a
    # room, units and stairs against a wall) and the sectors swept by door leaves. A region holding a room name is a room.
    keep, gifts = [], []
    for c in cands:
        inside_other = any(o is not c and o["ext"].contains(c["ext"]) for o in cands)
        named = any(c["free"].contains(Point(lb["x"], lb["y"])) for lb in labels)
        furniture = c["wall_frac"] < FURNITURE_WALL_FRAC and c["area"] < FURNITURE_M2
        if not named and ((inside_other and c["wall_frac"] < 0.6) or furniture):
            gifts.append(c["ext"])
            continue
        keep.append(c)

    # Each label belongs to the region it sits in; a label on a light line snaps to the nearest region.
    assigned: dict[int, list[dict]] = {i: [] for i in range(len(keep))}
    for lb in labels:
        pt = Point(lb["x"], lb["y"])
        home = next((i for i, c in enumerate(keep) if c["free"].contains(pt)), None)
        if home is None and keep:
            d, i = min((c["free"].distance(pt), i) for i, c in enumerate(keep))
            if d <= LABEL_SNAP_PT:
                home = i
        if home is not None:
            assigned[home].append(lb)

    finals: list[dict] = []
    for i, c in enumerate(keep):
        inside = assigned[i]
        if c["wall_frac"] < 0.3 and not inside:
            continue                                   # unlabelled and not bounded by walls: not a room
        nested = [o["ext"] for o in keep if o is not c and c["ext"].contains(o["ext"])]   # rooms walled off inside this one
        if len(inside) >= 2:
            for p in _split_by_seeds(c["free"], inside, solid):
                finals.append({"poly": _cut(Polygon(p["poly"].exterior), nested), "name": p["seed"]["text"], "split": True})
        else:
            finals.append({"poly": _cut(c["ext"], nested), "name": inside[0]["text"] if inside else None, "split": False})

    for sec, tip in sectors:                           # a door swing belongs to the room it opens into
        probe = Point(tip).buffer(1.5)
        home = next((f for f in finals if f["poly"].intersects(probe)), None)
        if home is None and finals:
            home = min(finals, key=lambda f: f["poly"].distance(Point(tip)))
            home = home if home["poly"].distance(Point(tip)) <= 12 else None
        if home is not None:
            merged = _largest(unary_union([home["poly"], sec.buffer(0.6)]).buffer(0))
            if merged is not None:
                home["poly"] = Polygon(merged.exterior)
    for g in gifts:                                    # give each non-room region back to the room it touches most
        grown = g.buffer(2.5)
        best = max(finals, key=lambda f: f["poly"].intersection(grown).area, default=None)
        if best is not None and best["poly"].intersection(grown).area > 0:
            merged = _largest(unary_union([best["poly"], grown]).buffer(0))
            if merged is not None:
                best["poly"] = Polygon(merged.exterior)
    rooms = [_room(f["poly"], s, f["name"], solid, split=f["split"]) for f in finals]
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
