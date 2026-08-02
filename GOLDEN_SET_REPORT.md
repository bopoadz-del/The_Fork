# GOLDEN_SET_REPORT — TASK 4e pilot gate

Generated 2026-08-02 03:02 UTC from golden_set_results.jsonl.

## Score: 28/29 PASS

Gate bar: >= 90% of the golden set must PASS (>= 27/29). **Gate MET.**

| id | feature | project | verdict | retrieval | latency |
|---|---|---|---|---|---|
| fresh_pier_mix_cement | fresh_upload_grounding | FIXTURE — Fresh Upload Eval | PASS | hit | 10s |
| fresh_r9_lighting_wattage | fresh_upload_grounding | FIXTURE — Fresh Upload Eval | PASS | hit | 13s |
| fresh_eot_notice_period | fresh_upload_grounding | FIXTURE — Fresh Upload Eval | PASS | hit | 12s |
| fresh_s4_manhole_spacing | fresh_upload_grounding | FIXTURE — Fresh Upload Eval | PASS | hit | 15s |
| fresh_design_review_codes | fresh_upload_grounding | FIXTURE — Fresh Upload Eval | PASS | hit | 10s |
| fresh_excavation_rate | fresh_upload_grounding | FIXTURE — Fresh Upload Eval | PASS | hit | 9s |
| fresh_rebar_cover | fresh_upload_grounding | FIXTURE — Fresh Upload Eval | PASS | hit | 10s |
| fresh_wearing_course | fresh_upload_grounding | FIXTURE — Fresh Upload Eval | PASS | hit | 10s |
| fresh_permit_to_work | fresh_upload_grounding | FIXTURE — Fresh Upload Eval | PASS | hit | 8s |
| fresh_mv_soak_test | fresh_upload_grounding | FIXTURE — Fresh Upload Eval | PASS | hit | 9s |
| fresh_cpi_threshold | fresh_upload_grounding | FIXTURE — Fresh Upload Eval | PASS | hit | 7s |
| fresh_mass_concrete_curing | fresh_upload_grounding | FIXTURE — Fresh Upload Eval | PASS | hit | 9s |
| pilot_doc_qa_dg2_pep | process_document | dar_al_arkan_master | PASS | - | 76s |
| pilot_boq_total | boq_process | FIXTURE — BOQ | PASS | - | 8s |
| pilot_spec_extraction | spec_analyze | dar_al_arkan_master | PASS | - | 40s |
| pilot_document_metadata | document_metadata | dar_al_arkan_master | PASS | - | 12s |
| pilot_wbs_generation | generate_wbs | dar_al_arkan_master | PASS | - | 3s |
| pilot_manpower_histogram | resource_histogram | dar_al_arkan_master | PASS | - | 4s |
| pilot_milestones | parse_primavera_schedule | FIXTURE — Programme+Drawings | PASS | - | 84s |
| pilot_s_curve | cash_flow_forecast | dar_al_arkan_master | PASS | - | 39s |
| pilot_procurement_list | procurement_list_generator | dar_al_arkan_master | PASS | - | 15s |
| pilot_rfp_sections | rfp_management | dar_al_arkan_master | PASS | - | 108s |
| pilot_kb_mass_concrete | construction_advisor | dar_al_arkan_master | PASS | - | 126s |
| pilot_qto_floor_area | drawing_qto | FIXTURE — Programme+Drawings | FAIL | - | 46s |
| demo_project_brief | demo_flow | dar_al_arkan_master | PASS | - | 30s |
| demo_biggest_cost_items | demo_flow | dar_al_arkan_master | PASS | - | 183s |
| demo_open_risks | demo_flow | dar_al_arkan_master | PASS | - | 69s |
| demo_procure_first | demo_flow | dar_al_arkan_master | PASS | - | 51s |
| cost_fabrication_concrete_rate | cost_grounding | dar_al_arkan_master | PASS | - | 31s |

## Failures

### pilot_qto_floor_area (drawing_qto)

- prompt: measure the floor area from the ground floor plan drawing
- expected (all must match): `(drawing|\.dxf|floor plan)`; `\d+(?:[.,]\d+)?\s*(?:m2|m²|sqm|square met)`
- what failed:
  - answer_expect not matched: '\\d+(?:[.,]\\d+)?\\s*(?:m2|m²|sqm|square met)'

What came back (verbatim excerpt):

```
_This project has no documents of its own for this question — answering from the Master Corpus._

The provided reference context does not contain the ground floor plan drawing or any measurements from which to calculate a floor area. The retrieved chunks consist of:

*   An electrical drawing legend and general notes (drawing IP-INF-053-0000-JCB-DWG-EL-200-3101112)  
*   Landscape drawing notes regarding levels and datum  
*   A list of architectural abbreviations (FFL, FGL, NTS, etc.)  
*   Structural notes for pedestrian underpass top-slab drawings (IP-INF-054-0000-JCB-DWG-ST-200-0000902-02 and IP-INF-054-0000-JCB-DWG-ST-200-0000952-05)

None of these documents are the ground floor plan, and none provide dimensions or area quantities for a ground floor slab. Therefore, I cannot provide t
[... truncated ...]
```
