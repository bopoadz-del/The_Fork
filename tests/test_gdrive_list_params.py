"""Ingest ``files.list`` params. A shared-drive folder 400s without them.

``walk_folder`` → ``list_folder_files`` is the call behind
``gdrive walk(<id>): Drive list returned 400``. The query must quote the
folder id, keep ``trashed = false``, stay at or under pageSize 1000, and
set ``supportsAllDrives`` together with ``includeItemsFromAllDrives``.
``corpora=drive`` is not sent: that parameter 400s unless ``driveId`` is
also sent, and a parent query does not need a drive id.
"""
from __future__ import annotations

import sys
import types

from app.core import gdrive_service as gds

FOLDER = "1GH3ri2gfPultO9FG56MdsLC7-7SvJB9j"
TARGET = "1TargetFolderIdForTheShortcutXXXX"
LIST_URL = "https://www.googleapis.com/drive/v3/files"

EXPECTED = {
    "q": f"'{FOLDER}' in parents and trashed = false",
    "pageSize": 200,
    "fields": (
        "nextPageToken, files(id, name, mimeType, size, "
        "modifiedTime, md5Checksum, etag)"
    ),
    "supportsAllDrives": "true",
    "includeItemsFromAllDrives": "true",
}


class _Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
        self.text = "" if status == 200 else "Invalid Value"
        self.content = b"{}"

    def json(self):
        return self._payload


class _Client:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, headers=None, params=None):
        self.calls.append({"url": url, "params": dict(params or {})})
        status, payload = self.script.pop(0)
        return _Resp(status, payload)


def _patch(monkeypatch, script):
    client = _Client(script)
    monkeypatch.setattr(gds, "_mint_access_token", lambda: "tok")
    mod = types.ModuleType("httpx")

    def _client(timeout=30):
        return client

    mod.Client = _client
    monkeypatch.setitem(sys.modules, "httpx", mod)
    return client


def test_the_ingest_list_sends_the_shared_drive_params(monkeypatch):
    client = _patch(monkeypatch, [
        (200, {"files": [{"id": "f1", "name": "a.pdf"}]}),
    ])
    files, err = gds.list_folder_files(FOLDER, page_size=200)
    assert err is None
    assert [f["id"] for f in files] == ["f1"]
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["url"] == LIST_URL
    assert call["params"] == EXPECTED
    assert "corpora" not in call["params"]
    assert "driveId" not in call["params"]
    assert "orderBy" not in call["params"]
    assert "pageToken" not in call["params"]


def test_a_later_page_sends_page_token_and_the_same_query(monkeypatch):
    client = _patch(monkeypatch, [
        (200, {"files": [{"id": "f1"}], "nextPageToken": "p2"}),
        (200, {"files": [{"id": "f2"}]}),
    ])
    files, err = gds.list_folder_files(FOLDER, page_size=5000)
    assert err is None
    assert [f["id"] for f in files] == ["f1", "f2"]
    assert client.calls[0]["params"]["pageSize"] == 1000
    second = dict(EXPECTED)
    second["pageSize"] = 1000
    second["pageToken"] = "p2"
    assert client.calls[1]["params"] == second


def test_a_shortcut_id_is_listed_via_its_folder_target(monkeypatch):
    client = _patch(monkeypatch, [
        (400, {}),
        (200, {
            "id": FOLDER,
            "mimeType": "application/vnd.google-apps.shortcut",
            "shortcutDetails": {
                "targetId": TARGET,
                "targetMimeType": "application/vnd.google-apps.folder",
            },
        }),
        (200, {"files": [{"id": "inside"}]}),
    ])
    files, err = gds.list_folder_files(FOLDER, page_size=200)
    assert err is None
    assert [f["id"] for f in files] == ["inside"]
    assert client.calls[0]["params"] == EXPECTED
    assert client.calls[1]["url"] == f"{LIST_URL}/{FOLDER}"
    assert client.calls[1]["params"] == {
        "fields": "id,mimeType,shortcutDetails",
        "supportsAllDrives": "true",
    }
    listed = dict(EXPECTED)
    listed["q"] = f"'{TARGET}' in parents and trashed = false"
    assert client.calls[2]["params"] == listed


def test_a_file_id_is_not_queried_as_a_parent(monkeypatch):
    client = _patch(monkeypatch, [
        (400, {}),
        (200, {"id": FOLDER, "mimeType": "application/pdf"}),
    ])
    files, err = gds.list_folder_files(FOLDER, page_size=200)
    assert files == []
    assert err is not None
    assert "not a folder" in err
    assert len(client.calls) == 2
