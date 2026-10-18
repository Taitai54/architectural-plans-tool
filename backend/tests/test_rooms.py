import io
import json
import sys
from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))
import autodetect  # noqa: E402
import extract  # noqa: E402
import main  # noqa: E402
import ocr  # noqa: E402
import rooms  # noqa: E402

S = 10.0   # mm per point in the synthetic scenes: 300 x 400 pt is 3 m x 4 m = 12 m2


def rect(x0, y0, x1, y1):
    return [x0, y0, x1, y0, x1, y0, x1, y1, x1, y1, x0, y1, x0, y1, x0, y0]


def scene(walls, detail=None, arcs=None):
    return {"width": 842, "height": 595, "views": [{"id": "v1", "kind": "drawing", "bbox": [0, 0, 842, 595]}],
            "walls": walls, "detail": detail or [], "arcs": arcs or [], "mass": {"cell": 8, "cells": []}}


PLAN = [{"text": "GROUND FLOOR PLAN", "bbox": [100, 520, 300, 540]}]      # the drawing's own title marks it as a plan


def label(text, x, y):
    return {"text": text, "x": x, "y": y, "h": 14, "known": True, "conf": 0.9}


def areas(res):
    """Sorted areas; compare with approx because wall lines have a little thickness (11.94 for a 12 m2 room)."""
    return pytest.approx(sorted(r["area_m2"] for r in res["rooms"]), rel=0.02)


def two_rooms_with_door_gap():
    # one outer box, a dividing wall at x=400 with a 900 mm gap between y=250 and y=340
    return rect(100, 100, 700, 500) + [400, 100, 400, 250, 400, 340, 400, 500]


DOOR = [400, 340, 490, 250, 400, 250, 90]      # quarter arc: closed-leaf tip, open tip, hinge, radius (0.9 m)


def test_single_closed_room():
    res = rooms.detect_rooms(scene(rect(100, 100, 400, 500)), S, [], texts=PLAN)
    assert areas(res) == [12.0] and res["rooms"][0]["named"] is False and res["rooms"][0]["boundary"] == "walls"


def test_door_arc_closes_the_doorway_into_two_rooms():
    labs = [label("Kitchen", 250, 300), label("Lounge", 550, 300)]
    sc = scene(two_rooms_with_door_gap(), arcs=DOOR)
    res = rooms.detect_rooms(sc, S, labs)
    assert sorted(r["name"] for r in res["rooms"]) == ["Kitchen", "Lounge"] and areas(res) == [12.0, 12.0]
    assert all(r["boundary"] == "walls" for r in res["rooms"])           # sealed by walls + door, nothing estimated
    # Without the door arc the doorway is open: the same label seeds split it, and the edge is flagged as estimated.
    res2 = rooms.detect_rooms(scene(two_rooms_with_door_gap()), S, labs)
    assert sorted(r["name"] for r in res2["rooms"]) == ["Kitchen", "Lounge"]
    assert sum(r["area_m2"] for r in res2["rooms"]) == pytest.approx(24.0, rel=0.06)
    assert all(r["boundary"] == "walls" for r in res2["rooms"])        # only a doorway is open, so the edge is mostly real wall


def test_open_plan_is_split_by_named_seeds_and_each_piece_holds_its_label():
    from shapely.geometry import Point, Polygon
    walls = rect(100, 100, 700, 500)                          # one big room, no dividing wall at all
    res = rooms.detect_rooms(scene(walls), S, [label("Kitchen", 200, 300), label("Dining", 600, 300)])
    by = {r["name"]: r for r in res["rooms"]}
    assert Polygon(by["Kitchen"]["poly"]).contains(Point(200, 300)) and Polygon(by["Dining"]["poly"]).contains(Point(600, 300))
    assert not Polygon(by["Kitchen"]["poly"]).contains(Point(600, 300))
    assert sum(r["area_m2"] for r in res["rooms"]) == pytest.approx(24.0, rel=0.06)
    assert by["Kitchen"]["area_m2"] == pytest.approx(by["Dining"]["area_m2"], rel=0.2)      # the edge lands midway
    assert by["Kitchen"]["boundary"] == by["Dining"]["boundary"] == "estimated"             # with no wall between them, say so


def test_furniture_island_does_not_leave_a_hole_or_a_room():
    island = rect(200, 200, 300, 260)
    res = rooms.detect_rooms(scene(rect(100, 100, 400, 500), detail=island), S, [], texts=PLAN)
    assert len(res["rooms"]) == 1 and res["rooms"][0]["area_m2"] == pytest.approx(12.0, rel=0.02)


def test_dashed_lines_do_not_cut_a_room():
    dashes = []
    for i in range(20):                                       # short, unconnected strokes across the room: roof lines, not walls
        x = 110 + i * 14
        dashes += [x, 300, x + 8, 300]
    res = rooms.detect_rooms(scene(rect(100, 100, 400, 500), detail=dashes), S, [], texts=PLAN)
    assert areas(res) == [12.0]


def test_connected_light_lines_do_close_openings():
    # a window drawn across a gap in the wall (a connected light line) must keep the room sealed
    walls = [100, 100, 400, 100, 400, 100, 400, 500, 400, 500, 100, 500, 100, 500, 100, 250, 100, 330, 100, 100]
    window = [100, 250, 100, 330]
    res = rooms.detect_rooms(scene(walls, detail=window + [95, 250, 105, 250, 95, 330, 105, 330]), S, [], texts=PLAN)
    assert areas(res) == [12.0]


def test_labelled_room_inside_a_bigger_one_is_kept_and_cut_out():
    walls = rect(100, 100, 700, 500) + rect(100, 100, 420, 270)         # a closet in the corner shares two walls with the hall
    res = rooms.detect_rooms(scene(walls), S, [label("Cloaks", 200, 200), label("Hall", 550, 400)])
    by = {r["name"]: r for r in res["rooms"]}
    assert by["Cloaks"]["area_m2"] == pytest.approx(5.44, rel=0.03)
    assert by["Hall"]["area_m2"] == pytest.approx(24.0 - 5.44, rel=0.05)    # not counted twice


def test_tiny_and_thin_regions_are_ignored():
    res = rooms.detect_rooms(scene(rect(100, 100, 150, 150) + rect(300, 100, 310, 500)), S, [], texts=PLAN)
    assert res["rooms"] == []


def test_elevations_are_skipped_using_the_drawing_title():
    sc = scene(rect(100, 100, 400, 500), arcs=DOOR + DOOR + DOOR)
    texts = [{"text": "NORTH EAST ELEVATION", "bbox": [150, 520, 350, 550]}]
    res = rooms.detect_rooms(sc, S, [], texts=texts)
    assert res["rooms"] == [] and any("elevation" in n for n in res["notes"])
    assert rooms.view_hint(texts, (100, 100, 400, 500)) == "elevation"
    assert rooms.view_hint([{"text": "FIRST FLOOR PLAN", "bbox": [150, 520, 350, 550]}], (100, 100, 400, 500)) == "plan"


def test_each_drawing_uses_its_own_scale():
    sc = scene(rect(100, 100, 400, 500))
    assert areas(rooms.detect_rooms(sc, lambda x, y: 20.0, [], texts=PLAN)) == [48.0]
    assert rooms.detect_rooms(sc, lambda x, y: None, [], texts=PLAN).get("rooms") == []


# ── extraction of door arcs from a real (synthetic) PDF ───────────────────────
def pdf_with_room(door=True):
    doc = pymupdf.open()
    page = doc.new_page(width=842, height=595)
    sh = page.new_shape()
    sh.draw_rect(pymupdf.Rect(100, 100, 500, 400))
    sh.finish(color=(0, 0, 0), width=0.72)
    if door:                                              # a quarter-circle swing: hinge (500, 300), radius 51 pt
        k = 0.5523 * 51
        sh.draw_bezier((500, 351), (500 - k, 351), (449, 300 + k), (449, 300))
        sh.finish(color=(0, 0, 0), width=0.24, closePath=False)
    sh.draw_bezier((600, 100), (650, 100), (700, 150), (700, 100))      # not an arc
    sh.finish(color=(0, 0, 0), width=0.24, closePath=False)
    sh.commit()
    return doc.tobytes()


def test_quarter_arcs_are_extracted_with_hinge_and_radius():
    doc = pymupdf.open(stream=pdf_with_room(), filetype="pdf")
    arcs = extract.extract_page(doc[0])["arcs"]
    assert len(arcs) == 7
    x0, y0, x1, y1, hx, hy, r = arcs
    assert r == pytest.approx(51, abs=1) and (hx, hy) == pytest.approx((500, 300), abs=1.5)
    assert extract.quarter_arc((0, 0), (10, 0), (20, 0), (30, 0)) is None


# ── OCR helpers ───────────────────────────────────────────────────────────────
def item(text, x0, y0, x1, y1, conf=0.9):
    return {"text": text, "conf": conf, "bbox": [x0, y0, x1, y1]}


def test_parse_scale():
    got = ocr.parse_scale([item("scale", 0, 0, 10, 5), item("1:50 @ A1", 0, 0, 10, 5)], 2384, 1684)
    assert got["ratio"] == 50 and got["paper"] == "A1" and got["page_matches_paper"] is True
    wrong = ocr.parse_scale([item("1:100 @ A3", 0, 0, 10, 5)], 2384, 1684)
    assert wrong["page_matches_paper"] is False                 # a reduced print: the ratio no longer applies directly
    bare = ocr.parse_scale([item("Scale 1:200", 0, 0, 10, 5)])
    assert bare["ratio"] == 200 and bare["paper"] is None and bare["page_matches_paper"] is None
    assert ocr.parse_scale([item("11:5000000", 0, 0, 1, 1), item("area 1 : 1", 0, 0, 1, 1)]) is None


def test_room_labels_keep_names_and_drop_tags_notes_and_vertical_text():
    items = [item("Reception 2", 0, 0, 105, 19), item("Dining Area", 0, 30, 100, 49), item("Utility", 0, 60, 60, 79),
             item("D05", 0, 0, 20, 8), item("W02", 0, 0, 20, 8), item("1350", 0, 0, 30, 8), item("Rooflight", 0, 0, 44, 14),
             item("Ceiling slope", 0, 0, 14, 61), item("DRAFT", 0, 0, 120, 30), item("NORTH EAST ELEVATION", 0, 0, 300, 30),
             item("Step", 0, 0, 20, 8), item("Existing extract fan", 0, 0, 60, 8), item("Sliding shading panel", 0, 0, 90, 8),
             item("Bedroom 1", 0, 0, 60, 9)]
    names = sorted(l["text"] for l in ocr.room_labels(items))
    assert names == ["Bedroom 1", "Dining Area", "Reception 2", "Utility"]


def test_tiles_cover_the_region_with_overlap():
    tiles = list(ocr._tiles((0, 0, 3000, 2000)))
    assert len(tiles) >= 4
    assert min(t[0] for t in tiles) == 0 and max(t[2] for t in tiles) == pytest.approx(3000) and max(t[3] for t in tiles) == pytest.approx(2000)
    assert len(list(ocr._tiles((0, 0, 800, 500)))) == 1


def test_ocr_page_maps_pixels_back_to_points_and_removes_duplicates(tmp_path):
    pdf = tmp_path / "p.pdf"
    doc = pymupdf.open()
    doc.new_page(width=842, height=595)
    doc.save(pdf)
    calls = []

    def fake_engine(img):
        calls.append(img.shape)
        z = 150 / 72
        return [([[300, 200], [500, 200], [500, 250], [300, 250]], "Kitchen", 0.92),
                ([[10, 10], [20, 10], [20, 20], [10, 20]], "faint", 0.2)], None      # below the confidence floor

    out = ocr.ocr_page(str(pdf), 1, [(0, 0, 800, 500)], engine=fake_engine)
    assert len(out) == 1 and out[0]["text"] == "Kitchen"
    z = 150 / 72
    assert out[0]["bbox"] == pytest.approx([300 / z, 200 / z, 500 / z, 250 / z], abs=0.2)
    # two overlapping regions see the same word: it is reported once
    out2 = ocr.ocr_page(str(pdf), 1, [(0, 0, 800, 500), (0, 0, 800, 500)], engine=fake_engine)
    assert len(out2) == 1


# ── endpoint, with the OCR engine replaced ─────────────────────────────────────
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "OUTPUT_DIR", tmp_path)
    return TestClient(main.app)


def upload(client, data):
    return client.post("/api/upload", files={"file": ("p.pdf", io.BytesIO(data), "application/pdf")}).json()["job_id"]


FAKE_ITEMS = [item("1:50 @ A4", 600, 560, 680, 575), item("Kitchen", 270, 230, 330, 250)]


def test_detect_rooms_reads_the_scale_names_the_room_and_caches_the_ocr(client, monkeypatch):
    jid = upload(client, pdf_with_room())
    calls = []
    monkeypatch.setattr(ocr, "available", lambda: True)
    monkeypatch.setattr(ocr, "ocr_page", lambda *a, **k: calls.append(1) or FAKE_ITEMS)
    r = client.post(f"/api/jobs/{jid}/pages/1/detect-rooms")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scale_applied"]["ratio"] == 50 and body["ocr"]["ran"] is True
    assert [x["name"] for x in body["rooms"]] == ["Kitchen"]
    assert body["rooms"][0]["area_m2"] == pytest.approx(400 * 300 * (25.4 / 72 * 50) ** 2 / 1e6, rel=0.02)
    assert body["rooms"][0]["poly"][0].keys() == {"x", "y"} and body["rooms"][0]["default_selected"] is True
    # the scale was saved for the plan, not the title block, and the second run reuses the cached text
    page = client.get(f"/api/jobs/{jid}/pages/1").json()
    assert [v["ratio"] for v in page["views"] if v["kind"] == "drawing"] == [50]
    r2 = client.post(f"/api/jobs/{jid}/pages/1/detect-rooms").json()
    assert r2["ocr"]["cached"] is True and r2["scale_applied"] is None and len(calls) == 1
    # a result can be saved straight back as rooms
    room = {"id": "r1", "name": "Kitchen", "poly": body["rooms"][0]["poly"]}
    assert client.put(f"/api/jobs/{jid}/pages/1/state", json={"rooms": [room]}).status_code == 200


def test_without_a_scale_or_ocr_the_user_is_told_what_to_do(client, monkeypatch):
    jid = upload(client, pdf_with_room())
    monkeypatch.setattr(ocr, "available", lambda: False)
    r = client.post(f"/api/jobs/{jid}/pages/1/detect-rooms")
    assert r.status_code == 409 and "scale" in r.json()["detail"].lower()
    page = client.get(f"/api/jobs/{jid}/pages/1").json()
    vid = next(v["id"] for v in page["views"] if v["kind"] == "drawing")
    client.put(f"/api/jobs/{jid}/pages/1/views/{vid}", json={"ratio": 50})
    r = client.post(f"/api/jobs/{jid}/pages/1/detect-rooms")
    assert r.status_code == 200
    assert r.json()["rooms"] == [] or all(x["name"] is None for x in r.json()["rooms"])   # no OCR: rooms are unnamed
    assert any("OCR" in n for n in r.json()["notes"])


def test_a_scale_that_does_not_match_the_page_size_is_not_applied(client, monkeypatch):
    jid = upload(client, pdf_with_room())
    monkeypatch.setattr(ocr, "available", lambda: True)
    monkeypatch.setattr(ocr, "ocr_page", lambda *a, **k: [item("1:50 @ A1", 600, 560, 680, 575), item("Kitchen", 270, 230, 330, 250)])
    r = client.post(f"/api/jobs/{jid}/pages/1/detect-rooms")
    assert r.status_code == 409                     # page is A4, so "1:50 @ A1" does not describe this print
    assert r.json()["scale_suggestion"]["page_matches_paper"] is False


def test_scans_and_unknown_jobs(client):
    assert client.post(f"/api/jobs/{'0' * 32}/pages/1/detect-rooms").status_code == 404


# ── walls drawn as solid black shapes ("poche") ───────────────────────────────
def poche_plan_pdf(with_door=True):
    doc = pymupdf.open()
    page = doc.new_page(width=842, height=595)
    sh = page.new_shape()
    t = 11.0                                                     # wall thickness in points (about 200 mm at 1:50)

    def wall(x0, y0, x1, y1):
        sh.draw_rect(pymupdf.Rect(x0, y0, x1, y1))
        sh.finish(color=None, fill=(0, 0, 0))
    wall(100, 100, 700, 100 + t)                                 # top
    wall(100, 500 - t, 700, 500)                                 # bottom
    wall(100, 100, 100 + t, 500)                                 # left
    wall(700 - t, 100, 700, 500)                                 # right
    wall(400 - t / 2, 100, 400 + t / 2, 250)                     # dividing wall with a gap for a door
    wall(400 - t / 2, 340, 400 + t / 2, 500)
    if with_door:                                                # quarter-circle swing, hinge at the top jamb
        k = 0.5523 * 90
        sh.draw_bezier((400, 340), (400 + k, 340), (490, 250 + k), (490, 250))
        sh.finish(color=(0, 0, 0), width=0.24, closePath=False)
    sh.commit()
    return doc.tobytes()


def test_solid_black_walls_are_walls():
    sc = extract.extract_page(pymupdf.open(stream=poche_plan_pdf(), filetype="pdf")[0])
    assert len(sc["solids"]) == 6 and sc["arcs"]
    res = rooms.detect_rooms(sc, S, [label("Kitchen", 250, 300), label("Lounge", 550, 300)], texts=PLAN)
    assert sorted(r["name"] for r in res["rooms"]) == ["Kitchen", "Lounge"]
    inner = (300 - 11 - 5.5) * (400 - 22) * S * S / 1e6                   # one room between the wall faces
    assert all(r["area_m2"] == pytest.approx(inner, rel=0.06) for r in res["rooms"])
    assert all(r["boundary"] == "walls" for r in res["rooms"])           # sealed by poche walls plus the door swing


def test_white_and_page_sized_fills_are_not_walls():
    doc = pymupdf.open()
    page = doc.new_page(width=842, height=595)
    sh = page.new_shape()
    sh.draw_rect(page.rect); sh.finish(color=None, fill=(1, 1, 1))                       # white page background
    sh.draw_rect(pymupdf.Rect(100, 100, 200, 140)); sh.finish(color=None, fill=(0.9, 0.9, 0.9))   # light grey hatch fill
    sh.draw_rect(pymupdf.Rect(300, 300, 390, 312)); sh.finish(color=None, fill=(0, 0, 0))         # a real dark wall piece
    sh.commit()
    assert len(extract.extract_page(doc[0])["solids"]) == 1


def test_clash_checks_see_solid_walls_too():
    from planner import World
    sc = extract.extract_page(pymupdf.open(stream=poche_plan_pdf(), filetype="pdf")[0])
    sc["views"][0]["mm_per_pt"] = S
    state = {"rooms": [{"id": "r1", "name": "Lounge", "poly": [{"x": 411, "y": 111}, {"x": 689, "y": 111}, {"x": 689, "y": 489}, {"x": 411, "y": 489}]}],
             "items": [], "pins": []}
    w = World(sc, state, "o1")
    inside_wall = w.call("place_item", {"item": "cooker", "room": "r1", "anchor": {"type": "point", "x_mm": 1500, "y_mm": 1500}})
    assert inside_wall["ok"] is True                                    # open floor is fine
    r = w.check(w.room("r1"), 405, 180, 0, 600, 600, 0)                  # centred on the solid part of the dividing wall
    assert any("wall" in h for h in r["hard"])
