"""Real-project fixtures (2026-07-24, operator-supplied from the Drive mirror).

- ``ohdd_baseline_2013.xer``: a genuine P6 baseline programme (1,280
  activities). Exercises the schedule family on real data instead of the
  synthetic resource_loaded fixtures. Repo is private; the operator (data
  owner) supplied it for this purpose.
- Navisworks / DWG: the operator's BIM models are .nwd (no native IFC
  exists in the corpus — the "IFCs" folders on Drive are Issued-For-
  Construction drawings, not BIM). The honest paths those files hit are
  pinned here deterministically: .nwd -> convert-to-IFC guidance;
  .dwg without the ODA converter -> structured install guidance.
"""

from __future__ import annotations

import os

import pytest

from app.containers.construction import ConstructionContainer
from tests.conftest import requires_construction_kit

XER = os.path.join(os.path.dirname(__file__), "fixtures", "ohdd_baseline_2013.xer")


@pytest.fixture
def container():
    return ConstructionContainer()


@requires_construction_kit
class TestRealBaselineXer:
    @pytest.mark.asyncio
    async def test_parse_real_baseline_programme(self, container):
        result = await container.parse_primavera_schedule({"file_path": XER}, {})
        assert result["status"] == "success"
        s = result["summary"]
        assert s["total_activities"] == 1280
        assert s["critical_activities"] == 19
        assert s["project_duration"] == 459
        assert s["data_date"] == "2013-11-27"
        assert len(result["critical_path"]["activities"]) == 19

    @pytest.mark.asyncio
    async def test_real_programme_feeds_delay_analysis_shape(self, container):
        result = await container.parse_primavera_schedule({"file_path": XER}, {})
        # The parse deliverable carries the delay/risk sections downstream
        # actions consume — assert they exist and are list/dict shaped.
        assert isinstance(result["schedule_risks"], list)
        # A baseline programme has nothing to be delayed against — the key
        # must exist (downstream reads it) but None is the honest value here.
        assert "delay_analysis" in result
        assert isinstance(result["recovery_options"], list)


class TestNavisworksHonestRejection:
    @pytest.mark.asyncio
    async def test_nwd_gets_convert_to_ifc_guidance(self, tmp_path):
        from app.blocks.bim_extractor import BIMExtractorBlock

        fake = tmp_path / "federated_model.nwd"
        fake.write_bytes(b"not a real navisworks file")
        result = await BIMExtractorBlock().process({"file_path": str(fake)}, {})
        assert result["status"] == "error"
        assert ".nwd" in result["error"] or "Navisworks" in result["error"]
        assert "IFC" in result["error"]


class TestDwgWithoutConverterGuidance:
    @pytest.mark.asyncio
    async def test_dwg_without_oda_gives_install_guidance(self, tmp_path, monkeypatch):
        import shutil as _shutil
        from app.blocks.drawing_qto import DrawingQTOBlock

        monkeypatch.setattr(_shutil, "which", lambda *_a, **_k: None)
        fake = tmp_path / "site_plan.dwg"
        fake.write_bytes(b"AC1032 not really a dwg")
        result = await DrawingQTOBlock().process({"file_path": str(fake)}, {})
        assert result["status"] == "error"
        assert "ODA File Converter" in result["error"]
        assert ".dxf" in result["error"]
