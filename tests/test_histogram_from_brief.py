"""resource_histogram from a brief (golden pilot_manpower_histogram repro).

"produce a manpower histogram for the structure works over 12 months" used
to hit the bare container action, which honestly refuses without a .xer —
correct for the schedule-file contract, but the router had already
intercepted the turn, so the user got an error instead of a histogram.

The action now has a registry plan: build a norms-based WBS from the brief,
time-phase its crews, and deliver with explicit provenance ("planning
estimates, not site resource returns"). The .xer path and its
no-fabrication contract are untouched.
"""

from __future__ import annotations

import pytest

from tests.conftest import requires_construction_kit


@requires_construction_kit
class TestHistogramFromBrief:
    @pytest.mark.asyncio
    async def test_brief_yields_periodised_histogram(self):
        from app.core.predefined_reasoning import run_workflow
        from app.schemas.project_session import ProjectSession

        session = ProjectSession.new("t-hist", user_id="t")
        out = await run_workflow(
            "resource_histogram",
            {
                "message": "produce a manpower histogram for the structure works over 12 months",
                "params": {},
                "project_id": None,
                "document_ids": [],
                "deliverable": True,
            },
            session,
        )
        assert out["handled"] is True
        answer = out["answer"]
        # Provenance is non-negotiable: estimates must say so.
        assert "norms-derived" in answer
        assert "planning estimates" in answer
        # Periodised rows with headcounts.
        assert "workers" in answer
        assert "Peak manpower" in answer

    @pytest.mark.asyncio
    async def test_xer_contract_untouched(self):
        """The direct container action still honestly refuses without a file."""
        from app.containers.construction import ConstructionContainer

        result = await ConstructionContainer().resource_histogram({}, {})
        assert result["status"] == "error"
        assert "schedule_file" in result["error"]
