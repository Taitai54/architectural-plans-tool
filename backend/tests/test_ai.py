import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pymupdf
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))
import ai  # noqa: E402
import main  # noqa: E402
from planner import TOOLS  # noqa: E402
from test_planner import an, make_world  # noqa: E402


# ── agent loop with a scripted provider ───────────────────────────────────────
class Scripted(ai.Provider):
    id, label = "scripted", "Scripted"

    def __init__(self, turns):
        super().__init__("m")
        self.turns, self.seen = list(turns), []

    def start(self, system, user_text, tools):
        self.seen.append(("start", system, user_text, [t["name"] for t in tools]))
        return {}

    def step(self, h):
        return self.turns.pop(0)

    def add_results(self, h, turn, results):
        self.seen.append(("results", [r for _, r in results]))


def call(name, **args):
    return ai.ToolCall("c1", name, args)


def test_loop_executes_tools_and_returns_ops_and_message():
    w = make_world()
    p = Scripted([ai.Turn("", [call("place_item", item="cooker", room="r1", anchor=an("top"))]), ai.Turn("Proposed a cooker on the top wall.")])
    out = ai.run_agent(p, w, "put a cooker on the top wall")
    assert out["message"].startswith("Proposed") and out["rounds"] == 2
    assert [o["op"] for o in out["ops"]] == ["add_item"] and out["ops"][0]["item"]["source"] == "ai"
    kind, system, user, tools = p.seen[0]
    assert "Kitchen" in user and "REQUEST: put a cooker on the top wall" in user
    assert "find_space" in tools and "treat" in system.lower()


def test_tool_errors_are_fed_back_so_the_model_can_retry():
    w = make_world()
    p = Scripted([ai.Turn("", [call("place_item", item="cooker", room="Garage", anchor=an("top"))]),
                  ai.Turn("", [call("place_item", item="cooker", room="Kitchen", anchor=an("top"))]),
                  ai.Turn("Done.")])
    out = ai.run_agent(p, w, "x")
    results = [s for s in p.seen if s[0] == "results"]
    assert results[0][1][0]["ok"] is False and "Kitchen" in results[0][1][0]["error"]
    assert len(out["ops"]) == 1 and out["rounds"] == 3


def test_round_limit_is_enforced_and_reported():
    w = make_world()
    turns = [ai.Turn("", [call("find_space", room="r1", item="cooker")]) for _ in range(10)]
    out = ai.run_agent(Scripted(turns), w, "x", max_rounds=3)
    assert out["rounds"] == 3 and "maximum number of steps" in out["message"]


def test_refusal_becomes_a_user_visible_error():
    with pytest.raises(ai.AIError, match="declined"):
        ai.run_agent(Scripted([ai.Turn("", [], refused=True)]), make_world(), "x")


def test_conversation_history_is_included_but_capped():
    p = Scripted([ai.Turn("ok")])
    hist = [{"role": "user" if i % 2 == 0 else "assistant", "text": f"msg{i}"} for i in range(10)]
    ai.run_agent(p, make_world(), "again", hist)
    user = p.seen[0][2]
    assert "msg9" in user and "msg3" not in user


# ── demo parser ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize("prompt,expected", [
    ("put a fridge against the left wall of the kitchen", ("tall fridge", "left")),
    ("Place the cooker on the top wall", ("cooker", "top")),
    ("add a sofa in the middle of the room", ("sofa", None)),
])
def test_demo_parses_simple_phrases(prompt, expected):
    w = make_world()
    out = ai.run_agent(ai.DemoProvider(w), w, prompt)
    assert len(out["ops"]) == 1, out
    it = out["ops"][0]["item"]
    assert expected[0].split()[0] in it["name"].lower()
    assert it["status"] == "pending"


def test_demo_move_and_next_to_and_unrecognised():
    existing = {"id": "i1", "kind": "cooker", "name": "Cooker", "w": 600, "d": 600, "clear": 1000, "x": 300, "y": 300, "rot": 0,
                "option": "o1", "status": "accepted"}
    w = make_world(items=[existing])
    out = ai.run_agent(ai.DemoProvider(w), w, "move the cooker to the right wall")
    assert out["ops"][0]["item"]["replaces"] == "i1"
    w = make_world(items=[existing])
    out = ai.run_agent(ai.DemoProvider(w), w, "put the dishwasher next to the cooker")
    assert out["ops"] and out["ops"][0]["item"]["name"] == "Dishwasher"
    w = make_world()
    out = ai.run_agent(ai.DemoProvider(w), w, "make it prettier")
    assert out["ops"] == [] and "Demo mode" in out["message"]


# ── adapters, against fake SDK clients ─────────────────────────────────────────
def test_anthropic_adapter_request_and_response_shapes():
    seen = {}

    def create(**kw):
        seen.setdefault("calls", []).append(json.loads(json.dumps(kw, default=lambda o: "<obj>")))
        if len(seen["calls"]) == 1:
            content = [NS(type="text", text="Checking."), NS(type="tool_use", id="tu_1", name="find_space", input={"room": "r1", "item": "cooker"})]
            return NS(content=content, stop_reason="tool_use")
        return NS(content=[NS(type="text", text="All set.")], stop_reason="end_turn")

    client = NS(messages=NS(create=create))
    w = make_world()
    p = ai.AnthropicProvider("claude-opus-5-5", client)
    out = ai.run_agent(p, w, "where can the cooker go?")
    first, second = seen["calls"]
    assert first["model"] == "claude-opus-5-5" and first["system"] == ai.SYSTEM
    assert {t["name"] for t in first["tools"]} == {t["name"] for t in TOOLS} and "input_schema" in first["tools"][0]
    assert first["output_config"] == {"effort": "medium"} and "tool_choice" not in first   # no forced tool use
    tr = second["messages"][-1]["content"][0]
    assert tr["type"] == "tool_result" and tr["tool_use_id"] == "tu_1" and tr["is_error"] is False
    assert json.loads(tr["content"])["ok"] is True
    assert out["message"] == "All set."
    # non-Claude-5 model ids don't get the effort parameter
    seen.clear()
    ai.AnthropicProvider("claude-haiku-4-5", client).step(ai.AnthropicProvider("x", client).start("s", "u", TOOLS))
    assert "output_config" not in seen["calls"][0]


def test_openai_adapter_request_and_response_shapes():
    seen = []

    def create(**kw):
        seen.append(json.loads(json.dumps(kw, default=lambda o: "<obj>")))
        if len(seen) == 1:
            tc = NS(id="call_1", function=NS(name="place_item", arguments=json.dumps({"item": "cooker", "room": "r1", "anchor": an("top")})))
            return NS(choices=[NS(message=NS(content=None, tool_calls=[tc], refusal=None))])
        return NS(choices=[NS(message=NS(content="Placed it.", tool_calls=None, refusal=None))])

    client = NS(chat=NS(completions=NS(create=create)))
    w = make_world()
    out = ai.run_agent(ai.OpenAIProvider("gpt-test", client), w, "cooker on the top wall")
    assert out["message"] == "Placed it." and len(out["ops"]) == 1
    first, second = seen
    assert first["model"] == "gpt-test" and first["messages"][0]["role"] == "system"
    assert first["tools"][0]["type"] == "function" and "parameters" in first["tools"][0]["function"]
    roles = [m["role"] for m in second["messages"]]
    assert roles == ["system", "user", "assistant", "tool"] and second["messages"][3]["tool_call_id"] == "call_1"
    assert second["messages"][2]["tool_calls"][0]["function"]["name"] == "place_item"


def test_openai_bad_json_arguments_do_not_crash():
    tc = NS(id="c", function=NS(name="place_item", arguments="{not json"))
    client = NS(chat=NS(completions=NS(create=lambda **kw: NS(choices=[NS(message=NS(content="", tool_calls=[tc], refusal=None))]))))
    p = ai.OpenAIProvider("m", client)
    turn = p.step(p.start("s", "u", TOOLS))
    assert turn.calls[0].args == {}


def test_gemini_adapter_accepts_our_tool_schemas_and_round_trips():
    from google.genai import types
    seen = []

    def generate_content(**kw):
        seen.append(kw)
        if len(seen) == 1:
            part = types.Part.from_function_call(name="find_space", args={"room": "r1", "item": "cooker"})
            return NS(candidates=[NS(content=types.Content(role="model", parts=[part]))],
                      function_calls=[NS(id=None, name="find_space", args={"room": "r1", "item": "cooker"})])
        return NS(candidates=[NS(content=types.Content(role="model", parts=[types.Part.from_text(text="Found spots.")]))], function_calls=None)

    client = NS(models=NS(generate_content=generate_content))
    w = make_world()
    out = ai.run_agent(ai.GeminiProvider("gemini-test", client), w, "where can the cooker go?")
    assert out["message"] == "Found spots."
    cfg = seen[0]["config"]
    assert len(cfg.tools[0].function_declarations) == len(TOOLS)          # real SDK validated every schema
    assert cfg.automatic_function_calling.disable is True
    results = seen[1]["contents"][-2]       # the list is shared, so the model's final reply is last
    assert results.role == "user" and results.parts[0].function_response.name == "find_space"


# ── environment + secrets ──────────────────────────────────────────────────────
def test_env_file_loading_and_scrubbing(tmp_path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text('# comment\nANTHROPIC_API_KEY="sk-ant-secretvalue123456"\nOPENAI_API_KEY=oa-keyvalue-9999999\nEMPTY=\n')
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "already-set-wins")
    ai.load_env_file(f)
    import os
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-secretvalue123456" and os.environ["OPENAI_API_KEY"] == "already-set-wins"
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert "***" in ai.scrub("auth failed for sk-ant-abcdefghijklmnop1234")
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaSyTESTKEYVALUE1234567")
    assert "AIzaSyTESTKEYVALUE1234567" not in ai.scrub("bad key AIzaSyTESTKEYVALUE1234567 rejected")


# ── HTTP endpoint ──────────────────────────────────────────────────────────────
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "OUTPUT_DIR", tmp_path)
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    return TestClient(main.app)


def job_with_room(client):
    doc = pymupdf.open()
    page = doc.new_page(width=842, height=595)
    sh = page.new_shape()
    sh.draw_rect(pymupdf.Rect(100, 100, 500, 400))
    sh.finish(color=(0, 0, 0), width=0.72)
    sh.commit()
    jid = client.post("/api/upload", files={"file": ("p.pdf", io.BytesIO(doc.tobytes()), "application/pdf")}).json()["job_id"]
    scene = client.get(f"/api/jobs/{jid}/pages/1").json()
    vid = next(v["id"] for v in scene["views"] if v["kind"] == "drawing")
    client.put(f"/api/jobs/{jid}/pages/1/views/{vid}", json={"ratio": 50})
    state = {"rooms": [{"id": "r1", "name": "Kitchen", "poly": [{"x": 100, "y": 100}, {"x": 500, "y": 100}, {"x": 500, "y": 400}, {"x": 100, "y": 400}]}],
             "options": [{"id": "o1", "name": "Option A"}], "active_option": "o1"}
    return jid, state


def test_providers_endpoint_never_exposes_keys(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-supersecretkey-1234567890")
    r = client.get("/api/ai/providers")
    assert r.status_code == 200 and "supersecret" not in r.text
    by = {p["id"]: p for p in r.json()}
    assert by["openai"]["configured"] is True and by["anthropic"]["configured"] is False and by["demo"]["configured"] is True


def test_ai_endpoint_demo_end_to_end(client):
    jid, state = job_with_room(client)
    r = client.post(f"/api/jobs/{jid}/pages/1/ai", json={"prompt": "put a fridge against the left wall of the kitchen", "provider": "demo", "state": state})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ops"][0]["op"] == "add_item" and body["ops"][0]["item"]["source"] == "ai" and "Proposed" in body["message"]
    # the proposal is valid state: it can be saved straight back
    saved = dict(state, items=[body["ops"][0]["item"]])
    assert client.put(f"/api/jobs/{jid}/pages/1/state", json=saved).status_code == 200


def test_ai_endpoint_errors(client):
    jid, state = job_with_room(client)
    url = f"/api/jobs/{jid}/pages/1/ai"
    r = client.post(url, json={"prompt": "hi", "provider": "anthropic", "state": state})
    assert r.status_code == 400 and "ANTHROPIC_API_KEY" in r.json()["detail"]
    assert client.post(url, json={"prompt": "hi", "provider": "nope", "state": state}).status_code == 400
    assert client.post(url, json={"prompt": "", "provider": "demo", "state": state}).status_code == 422
    assert client.post(url, json={"prompt": "x" * 2001, "provider": "demo", "state": state}).status_code == 422
    assert client.post(url, json={"prompt": "hi", "provider": "demo", "model": "bad model!", "state": state}).status_code == 422
    assert client.post(f"/api/jobs/{'0' * 32}/pages/1/ai", json={"prompt": "hi", "state": state}).status_code == 404


def test_provider_failure_is_a_502_without_leaking_the_key(client, monkeypatch):
    jid, state = job_with_room(client)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-leakcheck-0123456789abcdef")

    class Boom(ai.OpenAIProvider):
        def step(self, h):
            raise RuntimeError("401 invalid key sk-leakcheck-0123456789abcdef")

    monkeypatch.setitem(ai.PROVIDERS, "openai", Boom)
    r = client.post(f"/api/jobs/{jid}/pages/1/ai", json={"prompt": "hi", "provider": "openai", "state": state})
    assert r.status_code == 502 and "leakcheck" not in r.text and "RuntimeError" in r.text


def test_catalogue_endpoint(client):
    r = client.get("/api/catalogue")
    assert r.status_code == 200 and any(c["name"] == "Cooker" and c["w"] == 600 for c in r.json())


def test_demo_move_finds_an_item_by_kind():
    existing = {"id": "i1", "kind": "fridge", "name": "Tall fridge-freezer", "w": 700, "d": 700, "clear": 900, "x": 300, "y": 300,
                "rot": 0, "option": "o1", "status": "accepted"}
    w = make_world(items=[existing])
    out = ai.run_agent(ai.DemoProvider(w), w, "move the fridge to the right wall")
    assert out["ops"] and out["ops"][0]["item"]["replaces"] == "i1"


def test_demo_next_to_resolves_by_kind_word():
    existing = {"id": "i1", "kind": "fridge", "name": "Tall fridge-freezer", "w": 700, "d": 700, "clear": 900, "x": 300, "y": 300,
                "rot": 0, "option": "o1", "status": "accepted"}
    w = make_world(items=[existing])
    out = ai.run_agent(ai.DemoProvider(w), w, "put the dishwasher next to the fridge")
    assert out["ops"] and out["ops"][0]["item"]["name"] == "Dishwasher"
