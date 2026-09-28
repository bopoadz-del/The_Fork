"""Drive walk drops OS metadata, Office locks, and WhatsApp media before assignment."""

from __future__ import annotations

import logging

import pytest

from app.core import gdrive_service as gds

FOLDER = "application/vnd.google-apps.folder"


def _file(fid: str, name: str, mime: str = "application/octet-stream") -> dict:
    return {"id": fid, "name": name, "mimeType": mime}


@pytest.fixture
def tree(monkeypatch):
    store: dict[str, list] = {}

    def fake_list(folder_id, page_size=100):
        if folder_id not in store:
            return [], f"not found: {folder_id}"
        return list(store[folder_id]), None

    monkeypatch.setattr(gds, "list_folder_files", fake_list)
    return store


def test_walk_drops_junk_classes_and_keeps_siblings(tree, caplog):
    """Three phrasings per junk class, plus names that must still be assigned."""
    tree["root"] = [
        _file("os1", "DESKTOP.INI"),
        _file("os2", "desktop.ini"),
        _file("os3", "Desktop.ini"),
        _file("os4", "Thumbs.db"),
        _file("os5", ".DS_Store"),
        _file("os6", "ehthumbs.db"),
        _file("lk1", "~$Budget.xlsx"),
        _file("lk2", "~$report.DOCX"),
        _file("lk3", ".~lock.notes.docx#"),
        _file("wa1", "IMG-20230512-WA0007.jpg"),
        _file("wa2", "img-20191231-wa0123.JPEG"),
        _file("wa3", "IMG-20240101-WA9999.jpg"),
        _file("wa4", "VID-20200101-WA0001.mp4"),
        _file("wa5", "AUD-20200202-WA0002.opus"),
        _file("wa6", "PTT-20200303-WA0003.m4a"),
        _file("wa7", "ptt-20200404-wa0004.AAC"),
        _file("keep1", "IMG_1234.jpg", "image/jpeg"),
        _file("keep2", "IMG-0042.jpg", "image/jpeg"),
        _file("keep3", "specification.pdf", "application/pdf"),
        _file("keep4", "notes.docx"),
        _file("keep5", "ST-WA-200.pdf", "application/pdf"),
        {"id": "sub", "name": "desktop.ini", "mimeType": FOLDER},
    ]
    tree["sub"] = [
        _file("keep6", "inside.pdf", "application/pdf"),
    ]

    with caplog.at_level(logging.INFO, logger="app.core.gdrive_service"):
        files, errors = gds.walk_folder("root")

    assert errors == []
    assert sorted(f["id"] for f in files) == [
        "keep1", "keep2", "keep3", "keep4", "keep5", "keep6",
    ]
    summary = "\n".join(
        r.getMessage() for r in caplog.records if "WALK_JUNK" in r.getMessage()
    )
    assert summary
    assert "os_metadata=6" in summary
    assert "office_lock=3" in summary
    assert "whatsapp=7" in summary
    assert ".ini" in summary
    assert ".db" in summary
    assert ".ds_store" in summary
    assert ".xlsx" in summary
    assert ".docx" in summary
    assert ".jpg" in summary
    assert ".jpeg" in summary
    assert ".mp4" in summary
    assert ".opus" in summary
    assert ".m4a" in summary
    assert ".aac" in summary
    for banned in (
        "DESKTOP.INI",
        "desktop.ini",
        "Desktop.ini",
        "IMG-20230512-WA0007.jpg",
        "img-20191231-wa0123.JPEG",
        "IMG-20240101-WA9999.jpg",
        "~$Budget.xlsx",
        ".~lock.notes.docx#",
    ):
        assert banned not in summary
