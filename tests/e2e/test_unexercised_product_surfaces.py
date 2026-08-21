"""Walk every leftover-campaign-dark product surface in one session.

These routes existed and many had isolated unit tests, but the leftover hat
battery never opened them as a product path (register, new project, Drive
OAuth status, admin 403, composer-adjacent APIs, right-panel preview,
exports, chain, MCP, redline, photos, feedback, memory, hydration, usage,
workflows, schedule, RAG search, debug, conversation clear, sandbox 403).

One registered user owns a throwaway project. No leftover corpus, no
re-ingest, no mock payloads — every call hits the live FastAPI app.
"""
from __future__ import annotations

import io
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from PIL import Image

from app.main import app
from tests.conftest import requires_construction_kit

_RUN = uuid.uuid4().hex[:10]
_FIXTURE_IFC = Path(__file__).resolve().parents[1] / "fixtures" / "sample_office.ifc"
_PREVIOUSLY_UNPINNED_HATS = (
    "learning",
    "supervision-proposal",
    "external-mcp",
    "contracts-manager",
    "document-ingestion",
    "heavy-reasoning",
    "smart-orchestrator",
)
_API_KEY = {"Authorization": "Bearer cb_dev_key"}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def session(client):
    """Register + login a unique user; create a blank project."""
    email = f"surfaces-{_RUN}@example.com"
    password = "surfaces-pass-12"
    r = client.post(
        "/v1/users/register",
        json={"email": email, "password": password, "display_name": "Surface Walker"},
    )
    assert r.status_code in (201, 409), r.text
    r = client.post(
        "/v1/users/resend-verification",
        json={"email": email},
    )
    assert r.status_code == 202, r.text
    r = client.post("/v1/users/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    r = client.get("/v1/users/me", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["email"] == email
    r = client.post(
        "/v1/projects",
        json={"name": f"Surface Cover {_RUN}", "client": "Cover"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    return {"headers": headers, "token": token, "email": email, "pid": pid}


def _png_bytes(color=(220, 30, 30)) -> bytes:
    img = Image.new("RGB", (64, 64), (255, 255, 255))
    for y in range(8, 24):
        for x in range(8, 40):
            img.putpixel((x, y), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _jpeg_bytes(color=(220, 30, 30)) -> bytes:
    img = Image.new("RGB", (64, 64), (255, 255, 255))
    for y in range(8, 24):
        for x in range(8, 40):
            img.putpixel((x, y), color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _xlsx_bytes() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "BOQ"
    ws.append(["Item", "Qty", "Unit", "Rate"])
    ws.append(["Excavation in trench", 81.2, "m3", 45])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_register_login_blank_project_and_drive_oauth_status(client, session):
    h, pid = session["headers"], session["pid"]
    r = client.post(
        "/v1/projects/from-drive",
        json={"name": "From Drive", "folder_id": "fake-folder"},
        headers=h,
    )
    assert r.status_code in (400, 409), r.text

    r = client.get("/v1/drive/status", headers=h)
    assert r.status_code == 200, r.text
    status = r.json()
    assert "connected" in status and "configured" in status
    assert status["connected"] is False

    r = client.get("/v1/drive/connect", headers=h)
    # No Google client in CI → 503; if secrets are present, JSON auth_url.
    assert r.status_code in (200, 503), r.text
    if r.status_code == 200:
        assert "auth_url" in r.json()

    r = client.post("/v1/drive/disconnect", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["was_connected"] is False

    r = client.get("/v1/drive/files", headers=h)
    assert r.status_code in (409, 503), r.text

    r = client.get(f"/v1/projects/{pid}", headers=h)
    assert r.status_code == 200, r.text


def test_admin_surfaces_are_forbidden_for_a_normal_user(client, session):
    h, pid = session["headers"], session["pid"]
    for method, path, json_body in (
        ("GET", "/v1/admin/corpus/collections", None),
        ("GET", "/v1/admin/drive/scan", None),
        ("POST", "/v1/admin/projects/approve-from-drive",
         {"folder_id": "x", "name": "Nope"}),
        ("POST", "/v1/admin/debug/project-reindex?project_id=" + pid, None),
        ("GET", "/v1/debug/env", None),
    ):
        if method == "GET":
            r = client.get(path, headers=h)
        else:
            r = client.post(path, json=json_body, headers=h)
        assert r.status_code == 403, f"{path} -> {r.status_code} {r.text[:200]}"


def test_right_panel_preview_redline_photo_and_attach(client, session):
    h, pid = session["headers"], session["pid"]

    r = client.post(
        f"/v1/projects/{pid}/documents",
        files={"file": ("cover_sheet.xlsx", _xlsx_bytes(),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=h,
    )
    assert r.status_code == 201, r.text
    xlsx_id = r.json()["document"]["id"]

    r = client.post(
        f"/v1/projects/{pid}/documents",
        files={"file": ("cover_note.txt", b"Cover sheet notes for Doc tab.", "text/plain")},
        headers=h,
    )
    assert r.status_code == 201, r.text
    txt_id = r.json()["document"]["id"]

    r = client.post(
        f"/v1/projects/{pid}/documents",
        files={"file": ("markup.png", _png_bytes(), "image/png")},
        headers=h,
    )
    assert r.status_code == 201, r.text
    png_id = r.json()["document"]["id"]

    r = client.get(f"/v1/projects/{pid}/documents", headers=h)
    assert r.status_code == 200, r.text
    names = {d["original_name"] for d in r.json()["documents"]}
    assert {"cover_sheet.xlsx", "cover_note.txt", "markup.png"} <= names

    r = client.get(f"/v1/projects/{pid}/documents/{xlsx_id}/preview", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "table"

    r = client.get(f"/v1/projects/{pid}/documents/{txt_id}/preview", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "text"
    assert "Cover sheet" in r.json()["text"]

    r = client.post(f"/v1/projects/{pid}/documents/{png_id}/redlines", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "has_markup" in body and "pages" in body

    r = client.post(
        "/v1/chat/analyze-photo",
        files={"file": ("site.jpg", _jpeg_bytes(), "image/jpeg")},
        headers=h,
    )
    assert r.status_code in (200, 503), r.text
    if r.status_code == 200:
        assert r.json().get("_product_name") == "Safety Observation AI v2"
        assert "observations" in r.json()


def test_chain_mcp_feedback_memory_hydration_usage_workflows_schedule_rag(
    client, session
):
    h, pid = session["headers"], session["pid"]

    r = client.post(
        "/v1/chain",
        json={
            "steps": [{"block": "translate", "params": {"target": "es"}}],
            "initial_input": "hello",
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json().get("status") in ("success", "completed") or r.json().get("success") is True

    r = client.post(
        "/v1/chain",
        json={"steps": [{"block": "sandbox", "params": {}}]},
        headers=h,
    )
    assert r.status_code == 403, r.text

    r = client.get("/mcp/info", headers=_API_KEY)
    assert r.status_code == 200, r.text
    info = r.json()
    assert "tool_count" in info and "tools" in info
    # /mcp/sse is a long-lived stream — do not drain it. Prove it is mounted.
    assert "/mcp/sse" in client.get("/openapi.json").json()["paths"]

    r = client.post(
        "/v1/feedback/route",
        json={
            "message": "generate a procurement list for the MEP package",
            "correct_action": "procurement_list_generator",
            "project_id": pid,
            "original_action": "estimate_costs",
        },
        headers=_API_KEY,
    )
    assert r.status_code in (200, 201), r.text

    r = client.get("/v1/memory/stats", headers=_API_KEY)
    assert r.status_code == 200, r.text
    r = client.post("/v1/memory/get", json={"key": "surface-cover"}, headers=_API_KEY)
    assert r.status_code not in (401, 404, 500), r.text

    r = client.get("/v1/hydration/latest", headers=_API_KEY)
    assert r.status_code == 200, r.text
    r = client.get("/v1/hydration/history", headers=_API_KEY)
    assert r.status_code == 200, r.text
    r = client.post("/v1/hydration/run", json={}, headers=_API_KEY)
    assert r.status_code in (200, 403, 503), r.text

    r = client.get("/v1/usage/today", headers=h)
    assert r.status_code == 200, r.text
    assert "tokens" in r.json()
    r = client.get("/v1/usage", headers=h)
    assert r.status_code == 200, r.text

    r = client.post(
        "/v1/workflows",
        json={
            "name": f"surface-wf-{_RUN}",
            "project_id": pid,
            "steps": [{"block": "translate", "params": {"target": "es"}}],
        },
        headers=h,
    )
    assert r.status_code == 201, r.text
    wid = r.json()["id"]
    assert client.get("/v1/workflows", headers=h).status_code == 200
    r = client.post(f"/v1/workflows/{wid}/run", json={"initial_input": "hello"}, headers=h)
    assert r.status_code in (200, 422), r.text
    assert client.delete(f"/v1/workflows/{wid}", headers=h).status_code == 200

    r = client.post(
        "/v1/schedule/cpm",
        json={
            "activities": [
                {"id": "A", "duration": 3, "predecessors": []},
                {"id": "B", "duration": 5, "predecessors": [{"predecessor_id": "A"}]},
            ]
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "success"

    r = client.post(
        "/v1/schedule/generate",
        json={"brief": "Two-storey site office, 6 months", "target_count": 40},
        headers=h,
    )
    assert r.status_code in (200, 422), r.text

    r = client.post(
        "/v1/rag/search",
        json={"query": "excavation trench", "project_id": pid, "k": 3},
        headers=_API_KEY,
    )
    assert r.status_code == 200, r.text
    assert "chunks" in r.json()


def test_conversation_export_clear_history_and_sandbox_gate(client, session):
    from app.core import agent_memory

    h, pid = session["headers"], session["pid"]
    cid = f"ws-{pid}-{int(time.time() * 1000)}"
    agent_memory.get_or_create_conversation(cid, "project-assistant", project_id=pid)
    agent_memory.append_message(cid, "user", "Export this conversation as a test.")
    agent_memory.append_message(
        cid, "assistant",
        "| Item | Qty |\n|---|---|\n| Excavation | 81.2 |\n\nAnswer complete.",
    )

    r = client.get(f"/v1/projects/{pid}/conversations", headers=h)
    assert r.status_code == 200, r.text
    ids = [c["id"] for c in r.json().get("conversations", [])]
    assert cid in ids, r.json()

    r = client.post(
        f"/v1/projects/{pid}/conversations/{cid}/export?format=docx",
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.headers.get("content-type", "").startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml"
    ) or r.content[:2] == b"PK"

    r = client.post(
        f"/v1/projects/{pid}/conversations/{cid}/export?format=xlsx",
        headers=h,
    )
    assert r.status_code == 200, r.text

    r = client.post(
        f"/v1/projects/{pid}/conversations/{cid}/clear",
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "cleared"

    r = client.post(
        "/v1/execute",
        json={"block": "sandbox", "input": "print(1)", "params": {}},
        headers=h,
    )
    assert r.status_code == 403, r.text

    r = client.get("/v1/agents", headers=h)
    assert r.status_code == 200, r.text
    names = {a["name"] for a in r.json()["agents"]}
    missing = [n for n in _PREVIOUSLY_UNPINNED_HATS if n not in names]
    assert not missing, f"hat catalog missing {missing}"
    for name in _PREVIOUSLY_UNPINNED_HATS:
        r = client.get(f"/v1/agents/{name}", headers=h)
        assert r.status_code == 200, f"{name} -> {r.status_code}"


@requires_construction_kit
def test_construction_calc_and_clash_via_execute(client, session):
    h, pid = session["headers"], session["pid"]
    r = client.post(
        "/v1/execute",
        json={
            "block": "construction",
            "input": {},
            "params": {
                "action": "construction_calc",
                "name": "wind_pressure",
                "wind_speed_m_s": 50,
                "code": "aci",
            },
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    inner = body.get("result") if isinstance(body.get("result"), dict) else body
    assert inner.get("status") in ("success", "ok") or "result" in inner or "value" in inner, body

    if not _FIXTURE_IFC.is_file():
        pytest.skip("sample_office.ifc fixture missing")
    r = client.post(
        "/v1/execute",
        json={
            "block": "construction",
            "input": {"file_path": str(_FIXTURE_IFC)},
            "params": {
                "action": "bim_clash_detection",
                "ifc_file": str(_FIXTURE_IFC),
                "run_clash_detection": True,
            },
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    clash = r.json()
    inner = clash.get("result") if isinstance(clash.get("result"), dict) else clash
    # Handler returns action "clash_detection" (not the route name bim_clash_detection).
    assert inner.get("status") == "success", clash
    assert (
        isinstance(inner.get("clash_summary"), dict)
        or "clashes" in inner
        or isinstance(inner.get("clashes"), list)
    ), clash
    assert "No IFC file" not in (inner.get("error") or "")
