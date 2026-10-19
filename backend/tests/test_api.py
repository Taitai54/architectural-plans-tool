import io
import sys
from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))
import main  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "OUTPUT_DIR", tmp_path)
    return TestClient(main.app)


def plan_pdf(pages: int = 1) -> bytes:
    """A small vector 'plan': heavy wall box, a hatch band, a door arc, outlined-text-like glyphs."""
    doc = pymupdf.open()
    for _ in range(pages):
        page = doc.new_page(width=842, height=595)
        sh = page.new_shape()
        sh.draw_rect(pymupdf.Rect(100, 100, 500, 400))
        sh.finish(color=(0, 0, 0), width=0.72)
        for i in range(60):  # dense hatch band along the left wall
            sh.draw_line((90, 100 + i * 3), (100, 103 + i * 3))
            sh.finish(color=(0, 0, 0), width=0.12, closePath=False)  # one path per stroke, as CAD exports do
        sh.draw_bezier((200, 400), (200, 360), (240, 360), (240, 400))
        sh.finish(color=(0, 0, 0), width=0.36)
        for i in range(5):  # glyph-like filled blobs forming a word
            sh.draw_rect(pymupdf.Rect(300 + i * 8, 200, 305 + i * 8, 210))
            sh.finish(fill=(0, 0, 0), color=None)
        sh.commit()
        page.insert_text((120, 450), "client note")
    return doc.tobytes()


def scan_pdf() -> bytes:
    src = pymupdf.open()
    sp = src.new_page(width=600, height=400)
    sp.draw_rect(pymupdf.Rect(50, 50, 300, 200))
    pix = sp.get_pixmap(dpi=40)
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=400)
    page.insert_image(page.rect, pixmap=pix)
    return doc.tobytes()


def upload(client, data: bytes, name="plan.pdf"):
    return client.post("/api/upload", files={"file": (name, io.BytesIO(data), "application/pdf")})


def test_upload_extracts_classified_geometry(client):
    r = upload(client, plan_pdf())
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["job_id"]) == 32 and body["pages"][0]["kind"] == "vector"
    scene = client.get(f"/api/jobs/{body['job_id']}/pages/1").json()
    counts = scene["counts"]
    assert counts["structure"] >= 1
    assert counts["hatch"] >= 20
    assert counts["text"] >= 5
    assert any("client note" in t["text"] for t in scene["texts"])
    assert scene["snaps"], "snap points should be emitted"


def test_multi_page(client):
    body = upload(client, plan_pdf(pages=3)).json()
    assert [p["page"] for p in body["pages"]] == [1, 2, 3]
    assert client.get(f"/api/jobs/{body['job_id']}/pages/3").status_code == 200
    assert client.get(f"/api/jobs/{body['job_id']}/pages/4").status_code == 404


def test_scan_is_flagged_and_previewable(client):
    body = upload(client, scan_pdf()).json()
    assert body["pages"][0]["kind"] == "scan"
    png = client.get(f"/api/jobs/{body['job_id']}/pages/1/preview.png")
    assert png.status_code == 200 and png.content[:4] == b"\x89PNG"


def test_scale_roundtrip_and_validation(client):
    jid = upload(client, plan_pdf()).json()["job_id"]
    scene = client.get(f"/api/jobs/{jid}/pages/1").json()
    vid = next(v["id"] for v in scene["views"] if v["kind"] == "drawing")
    url = f"/api/jobs/{jid}/pages/1/views/{vid}"
    r = client.put(url, json={"ratio": 50})
    assert r.status_code == 200
    assert r.json()["mm_per_pt"] == pytest.approx(25.4 / 72 * 50)
    again = client.get(f"/api/jobs/{jid}/pages/1").json()
    assert next(v for v in again["views"] if v["id"] == vid)["ratio"] == 50
    assert client.put(url, json={"ratio": -1}).status_code == 422
    assert client.put(url, json={"mm_per_pt": 0}).status_code == 422
    assert client.put(f"/api/jobs/{jid}/pages/1/views/v999", json={"ratio": 50}).status_code == 404


def test_rejects_non_pdf_and_leaves_no_files(client, tmp_path):
    assert upload(client, b"hello world", "x.pdf").status_code == 400
    assert upload(client, plan_pdf(), "x.txt").status_code == 400
    assert list(tmp_path.iterdir()) == []


def test_rejects_oversize(client, tmp_path, monkeypatch):
    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 1000)
    assert upload(client, plan_pdf()).status_code == 413
    assert list(tmp_path.iterdir()) == []


def test_rejects_corrupt_pdf(client, tmp_path):
    assert upload(client, b"%PDF-1.4\ngarbage").status_code == 400
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("bad", ["..", "%2e%2e", "not-a-job", "A" * 32, "../" + "a" * 32])
def test_job_ids_are_confined(client, bad):
    assert client.get(f"/api/jobs/{bad}").status_code == 404
    assert client.get(f"/api/jobs/{bad}/original.pdf").status_code == 404


def test_old_file_route_is_gone(client):
    assert client.get("/file/../main.py").status_code in (404, 405)
    assert client.get("/file/%2e%2e/.env").status_code in (404, 405)


def test_rotated_page_is_normalised(client):
    doc = pymupdf.open()
    page = doc.new_page(width=842, height=595)
    page.draw_rect(pymupdf.Rect(100, 100, 300, 200), width=0.72)
    page.set_rotation(90)
    jid = upload(client, doc.tobytes()).json()["job_id"]
    scene = client.get(f"/api/jobs/{jid}/pages/1").json()
    # Geometry and page size must share one coordinate frame (the unrotated sheet).
    assert (scene["width"], scene["height"]) == (842, 595)
    xs = [float(n) for p in scene["paths"] for n in p["d"].replace("M", " ").replace("L", " ").replace("H", " ").replace("V", " ").replace("Z", " ").split()]
    assert max(xs) <= 842


def test_state_roundtrip_and_validation(client):
    jid = upload(client, plan_pdf()).json()["job_id"]
    url = f"/api/jobs/{jid}/pages/1/state"
    assert client.get(f"/api/jobs/{jid}/pages/1").json()["state"]["rooms"] == []
    state = {
        "rooms": [{"id": "r1", "name": "Kitchen", "poly": [{"x": 0, "y": 0}, {"x": 100, "y": 0}, {"x": 100, "y": 80}]}],
        "items": [{"id": "i1", "kind": "fridge", "name": "Fridge", "w": 700, "d": 700, "clear": 900, "x": 50, "y": 40, "rot": 90}],
        "measures": [{"a": {"x": 0, "y": 0}, "b": {"x": 10, "y": 0}}],
        "custom_items": [],
    }
    assert client.put(url, json=state).status_code == 200
    got = client.get(f"/api/jobs/{jid}/pages/1").json()["state"]
    assert got["rooms"][0]["name"] == "Kitchen" and got["items"][0]["rot"] == 90
    bad = dict(state, items=[dict(state["items"][0], w=-5)])
    assert client.put(url, json=bad).status_code == 422
    bad = dict(state, rooms=[dict(state["rooms"][0], poly=[{"x": 0, "y": 0}])])
    assert client.put(url, json=bad).status_code == 422
    assert client.put(f"/api/jobs/{jid}/pages/9/state", json=state).status_code == 404


def test_walls_are_emitted(client):
    jid = upload(client, plan_pdf()).json()["job_id"]
    walls = client.get(f"/api/jobs/{jid}/pages/1").json()["walls"]
    assert len(walls) >= 16 and len(walls) % 4 == 0  # the heavy rectangle's four edges


def test_hatched_walls_are_exposed_as_mass(client):
    jid = upload(client, plan_pdf()).json()["job_id"]
    mass = client.get(f"/api/jobs/{jid}/pages/1").json()["mass"]
    assert mass["cell"] > 0 and len(mass["cells"]) >= 4 and len(mass["cells"]) % 2 == 0


def test_state_pins_options_and_item_status(client):
    jid = upload(client, plan_pdf()).json()["job_id"]
    url = f"/api/jobs/{jid}/pages/1/state"
    item = {"id": "i1", "kind": "fridge", "name": "Fridge", "w": 700, "d": 700, "clear": 900, "x": 50, "y": 40, "rot": 0}
    state = {
        "pins": [{"id": "p1", "x": 10, "y": 20, "text": "fridge where", "status": "open", "source": "pdf",
                  "thread": [{"text": "kitchen end?", "ts": 1.5}]}],
        "options": [{"id": "o1", "name": "Option A"}, {"id": "o2", "name": "Stove on the right"}],
        "active_option": "o2", "notes_imported": True,
        "items": [dict(item, option="o1", status="accepted")],
    }
    assert client.put(url, json=state).status_code == 200
    got = client.get(f"/api/jobs/{jid}/pages/1").json()["state"]
    assert got["pins"][0]["thread"][0]["text"] == "kitchen end?"
    assert got["active_option"] == "o2" and got["notes_imported"] is True
    assert got["items"][0]["status"] == "accepted" and got["items"][0]["option"] == "o1"
    # items saved before options existed still load (defaults)
    assert client.put(url, json={"items": [item]}).status_code == 200
    assert client.get(f"/api/jobs/{jid}/pages/1").json()["state"]["items"][0]["status"] == "pending"
    assert client.put(url, json={"items": [dict(item, status="bogus")]}).status_code == 422
    assert client.put(url, json={"pins": [{"id": "p1", "x": 0, "y": 0, "text": "x", "status": "closed"}]}).status_code == 422


def test_wrapped_lines_are_flagged(client):
    doc = pymupdf.open()
    page = doc.new_page(width=842, height=595)
    page.insert_text((50, 100), "first line of a wrapped note ", fontsize=10)
    page.insert_text((50, 112), "standalone", fontsize=10)
    jid = upload(client, doc.tobytes()).json()["job_id"]
    texts = {t["text"]: t["wrap"] for t in client.get(f"/api/jobs/{jid}/pages/1").json()["texts"]}
    assert texts["first line of a wrapped note"] is True
    assert texts["standalone"] is False


def noted_pdf(text: str) -> bytes:
    """The small plan (a real A4 landscape page) with a note added."""
    doc = pymupdf.open(stream=plan_pdf(), filetype="pdf")
    page = doc[0]
    page.insert_text((120, 480), text, fontsize=14)
    return doc.tobytes()


def test_read_scale_from_the_pdf_text(client):
    job = upload(client, noted_pdf("GROUND FLOOR PLAN 1:50 @ A4")).json()["job_id"]
    r = client.post(f"/api/jobs/{job}/pages/1/read-scale").json()
    assert r["applied"] and r["found"]["ratio"] == 50
    page = client.get(f"/api/jobs/{job}/pages/1").json()
    assert any(v["kind"] == "drawing" and v["ratio"] == 50 for v in page["views"])


def test_read_scale_only_suggests_when_the_paper_size_is_not_confirmed(client):
    job = upload(client, noted_pdf("Scale 1:50")).json()["job_id"]
    r = client.post(f"/api/jobs/{job}/pages/1/read-scale").json()
    assert not r["applied"] and r["found"]["ratio"] == 50
    assert client.post(f"/api/jobs/{job}/pages/1/read-scale").status_code == 200


# ── review fixes ──────────────────────────────────────────────────────────────
def test_foreign_host_and_origin_are_refused(client):
    assert client.get("/api/catalogue", headers={"host": "evil.example.com"}).status_code == 400
    r = client.post("/api/upload", files={"file": ("a.pdf", io.BytesIO(plan_pdf()), "application/pdf")},
                    headers={"origin": "https://evil.example.com"})
    assert r.status_code == 403
    assert upload(client, plan_pdf()).status_code == 200


def test_nan_in_state_is_rejected_and_page_stays_readable(client):
    job = upload(client, plan_pdf()).json()["job_id"]
    bad = '{"pins":[{"id":"p1","x":1,"y":1,"text":"t","created":NaN}]}'
    r = client.put(f"/api/jobs/{job}/pages/1/state", content=bad, headers={"content-type": "application/json"})
    assert r.status_code == 422
    assert client.get(f"/api/jobs/{job}/pages/1").status_code == 200


def test_concurrent_view_updates_do_not_lose_each_other(client):
    from concurrent.futures import ThreadPoolExecutor
    job = upload(client, plan_pdf()).json()["job_id"]
    vid = client.get(f"/api/jobs/{job}/pages/1").json()["views"][0]["id"]
    with ThreadPoolExecutor(4) as ex:
        list(ex.map(lambda i: client.put(f"/api/jobs/{job}/pages/1/views/{vid}", json={"name": "Ground floor" if i % 2 else "x"}), range(8)))
        client.put(f"/api/jobs/{job}/pages/1/views/{vid}", json={"ratio": 50})
    v = next(v for v in client.get(f"/api/jobs/{job}/pages/1").json()["views"] if v["id"] == vid)
    assert v["ratio"] == 50


def test_env_file_handles_bom_utf16_and_comments(tmp_path, monkeypatch):
    import ai
    for k in ("T_KEY_A", "T_KEY_B", "T_KEY_C"):
        monkeypatch.delenv(k, raising=False)
    (tmp_path / "a.env").write_bytes(b"\xef\xbb\xbfT_KEY_A=abc123  # my key\n")
    (tmp_path / "b.env").write_bytes("T_KEY_B=xyz789\r\n".encode("utf-16"))
    (tmp_path / "c.env").write_text('T_KEY_C="quoted # value"\n')
    for n in "abc":
        ai.load_env_file(tmp_path / f"{n}.env")
    import os
    assert os.environ["T_KEY_A"] == "abc123" and os.environ["T_KEY_B"] == "xyz789" and os.environ["T_KEY_C"] == "quoted # value"
    monkeypatch.delenv("T_KEY_A"); monkeypatch.delenv("T_KEY_B"); monkeypatch.delenv("T_KEY_C")


def test_public_url_hides_credentials():
    import ai
    assert ai.public_url("http://bob:hunter2@host:1234/v1?token=abc") == "http://host:1234/v1"


def test_dense_stipple_does_not_make_extraction_quadratic():
    import time
    import extract
    centres = [(float(i % 200) * 0.1, float(i // 200) * 0.1) for i in range(20000)]    # all in one cell
    t = time.time()
    near = extract._grid_neighbours(centres, 45.0, cap=6)
    assert time.time() - t < 3 and min(near) >= 6
