# commissioning_checklist — prompt 2, run 1

- verdict: FAIL
- prompt: what T&C steps do we need before energising the electrical rooms?
- project: dar_al_arkan_master
- conversation_id: fmx-12c788f92a3b
- route event: `{"requested": "project-assistant", "final": "project-assistant", "action": null, "confidence": 0.0, "reason": "below_routing_gate"}`
- tool_calls: [{"name": "commissioning_checklist", "args": "{\"systems\":[\"electrical\"]}", "id": null}]
- tool_results: [{"name": "commissioning_checklist", "ok": true, "duration_ms": 0, "error": null}]
- routing oracle: FAIL (ROUTE_MISS)
- execution oracle: PASS
- structure: {"has_checklist_items": "PASS", "cites_standards": "PASS"}
- first_token_s: 21.52  total_s: 21.52
- served_model: glm-5.2:cloud  fallback_used: False
- answer_chars: 2585  sources: 3  events: 39

## Answer (verbatim)

Here is the electrical T&C checklist that must be completed before energising the electrical rooms. All 7 tests are currently **pending**.

---

## Electrical Testing & Commissioning — Pre-Energisation Checklist

| # | Test | Standard | Acceptance Criteria | Witness Required? |
|---|------|----------|-------------------|-------------------|
| 1 | **Insulation Resistance** | IEEE 43 | > 1 MΩ | No |
| 2 | **Continuity Testing** | BS 7671 | R1+R2 < design value | No |
| 3 | **Earth Fault Loop** | BS 7671 | Zs < tabulated value | **Yes** |
| 4 | **RCD Testing** | BS 7671 | Trip time < 300 ms | **Yes** |
| 5 | **Load Bank Test** | IEEE 450 | Full load for 4 hours | **Yes** |
| 6 | **Power Quality** | IEEE 519 | THD < 5% | No |
| 7 | **Generator Auto-Start** | NFPA 110 | Start < 10 seconds | **Yes** |

---

### Key points

- **4 of 7 tests require a witness present** (Earth Fault Loop, RCD Testing, Load Bank Test, Generator Auto-Start). Arrange the witnessing engineer/consultant before scheduling these.
- **Logical sequence**: Insulation Resistance → Continuity → Earth Fault Loop → RCD → Load Bank → Power Quality → Generator Auto-Start. Dead tests (1–2) must pass before any live test (3 onward).
- **Documentation required**: `electrical_commissioning_report.pdf` — must be completed and submitted as part of the handover pack.
- **Operator training** for electrical systems is a commissioning requirement before final sign-off.
- **Final sign-off** is pending from: mechanical contractor, electrical contractor, fire contractor, commissioning authority, and client representative.

---

### Safety controls from the project method statements

The project's method statements for insitu concrete and related works reinforce the following electrical safety controls that apply during T&C activities:

- Conduct a thorough inspection of the electrical systems before starting any maintenance/testing activities.
- Use insulated tools and equipment.
- Implement a **lockout/tagout** procedure to isolate electrical circuits.
- Only **qualified electricians** should be permitted to perform electrical work.
- Use 3-core cables with proper earthing; cables should be protected from shared edges or protruding objects.
- All electrical equipment to be grounded.
- Before starting work, use **voltage testing equipment** to verify the circuit is de-energised; ensure testing equipment is regularly calibrated and in good working condition.

*(Sources: MOS cast Insitu Concrete - caw.pdf, chunks 384, 387; 3MTSFO~1.PDF, chunk 252; MOS cast Insitu Concrete - caw1.pdf, chunk 389)*
