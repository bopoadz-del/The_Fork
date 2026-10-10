"""Neon restore-point helper: fail closed without a secret; no values printed."""
from __future__ import annotations

import importlib
import io
import json
import sys
from pathlib import Path
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def _mod():
    return importlib.import_module("neon_restore_branch")


def test_missing_secret_is_clear(monkeypatch, capsys):
    monkeypatch.delenv("NEON_API_KEY", raising=False)
    monkeypatch.delenv("NEON_API_TOKEN", raising=False)
    mod = _mod()
    assert mod.main(["--name", "scrub-restore-test"]) == 2
    out = capsys.readouterr().out
    assert "neon_secret_missing" in out
    assert "NEON_API_KEY" in out
    assert "NEON_API_TOKEN" in out


def test_create_branch_prints_id_only(monkeypatch, capsys):
    mod = _mod()

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({
                "branch": {"id": "br_restore_syn"},
                "connection_uris": [{"connection_uri": "postgresql://hidden"}],
            }).encode()

    def opener(req, timeout=30):
        return _Resp()

    monkeypatch.setenv("NEON_API_KEY", "syn-key-not-real")
    monkeypatch.setenv("NEON_PROJECT_ID", "syn-project")
    branch = mod.create_restore_branch(
        "scrub-restore-1", key="syn-key-not-real",
        project_id="syn-project", opener=opener,
    )
    assert branch == "br_restore_syn"
    out = capsys.readouterr().out
    assert "postgresql://" not in out
    assert "hidden" not in out
    assert "syn-key-not-real" not in out


def test_http_error_does_not_print_body(monkeypatch, capsys):
    mod = _mod()

    def opener(req, timeout=30):
        raise HTTPError(req.full_url, 401, "no", hdrs=None, fp=io.BytesIO(b"secret-body"))

    monkeypatch.setenv("NEON_API_KEY", "syn-key-not-real")
    monkeypatch.setenv("NEON_PROJECT_ID", "syn-project")
    try:
        mod.create_restore_branch(
            "x", key="syn-key-not-real", project_id="syn-project", opener=opener,
        )
    except RuntimeError as exc:
        assert "neon_http_401" in str(exc)
        assert "secret-body" not in str(exc)
    else:
        raise AssertionError("expected RuntimeError")
    assert "secret-body" not in capsys.readouterr().out
