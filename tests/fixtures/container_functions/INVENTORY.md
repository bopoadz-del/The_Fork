# Construction container actions — inventory

Synthetic-only. No live project names, figures, or document text.

Source of truth: `ConstructionContainer.get_actions()` plus `route()` handlers.
Dropped from the real-action list: `health_check`, `status` (metadata).
Aliased names that call through a real handler are noted, not counted twice.

## Chat reachability (runtime.py)

Agents with `construction` in `allowed_blocks` get:

1. Generic tool `construction` — `{input, params: {action, …}}` for every
   container action.
2. Own typed tools (same agents): `generate_wbs`, `commissioning_checklist`,
   `cash_flow_forecast`, `resource_histogram`, `look_ahead`, `evm_calculate`,
   plus `construction_calc` (owned by another agent).
3. Predispatch (runtime intercept, no model tool pick): `look_ahead`,
   `generate_wbs`, `cash_flow_forecast`, `payment_certificate`,
   `rfi_generator`, `job_requisition`, `safety_briefing`,
   `commissioning_checklist`, `claims_builder`, `om_manual_generator`,
   `as_built_deviation_report`, `rfp_draft`, `wir_form`,
   `resource_histogram`.

Standalone block tools (not the container action name) when the agent
lists that block: `drawing_qto`, `boq_processor`, `primavera_parser`,
`spec_analyzer`, `bim_extractor`.

## Agents with `construction`

construction-pm, quantity-surveyor, contracts-manager, project-assistant,
bim-analyst, smart-orchestrator, document-analyst, safety-officer,
heavy-reasoning, supervision-proposal, validation.

Agents without the container (cannot call generic `construction`):
self-coding, external-mcp, learning, document-ingestion
(document-ingestion still has drawing_qto / boq_processor / primavera_parser /
spec_analyzer as own blocks).

## Aliases that call through (not separate actions)

| Alias | Calls |
|---|---|
| inspection_request | wir_form |
| rfp_management | rfp_draft |
| bim_extractor | bim_extract |
| contract_review | process_contract |
| safety_audit | safety_compliance_audit |
| carbon_report | generate_carbon_report (via carbon_footprint_calculator) |
| procurement | procurement_analysis |
| cost_estimate | generate_cost_estimate |
| analyze_spec | analyze_spec_section |
| schedule_risk | analyze_schedule_risk |
| payment_cert / generate_pay_app | payment_certificate (cm_step_aliases) |
| procurement_plan / link_procurement | procurement_list_generator |
| earned_value / cost_variance | evm_calculate |
| rfi_generation / generate_rfi | rfi_generator |
| primavera_parse | PrimaveraParserBlock (twin of parse_primavera_schedule) |

Not owned here: `construction_calc`, `formula_execute`, `sympy_reason`.

## Action table

Columns: chat reach / agents / det vs LLM / needs uploaded file.

| Action | Chat reach | Agents | Det/LLM | File? |
|---|---|---|---|---|
| look_ahead | own tool + generic + predispatch | construction-capable | det | yes (.xer) |
| procurement_list_generator | generic | construction-capable; QS prompt names it | det | no (BOQ/qty in payload) |
| drawing_qto | own block tool + generic action + file predispatch | any with drawing_qto or construction | det (block) | yes (dxf/dwg/pdf) |
| variation_order_manager | generic | construction-capable | det | optional contract file |
| payment_certificate | generic + IPC predispatch | construction-capable; contracts-manager prompt | det | no (figures) |
| generate_wbs | own tool + generic + predispatch | construction-capable; heavy-reasoning prompt | det | no |
| cash_flow_forecast | own tool + generic + predispatch | construction-capable; PM prompt | det | optional .xer |
| rfi_generator | generic + predispatch | construction-capable | det | no (issues or message) |
| evm_calculate | own tool + generic | construction-capable | det | no (PV/EV/AC) |
| boq_process | generic; chat also `boq_processor` block | construction-capable + QS/PM/ingestion | det (block) | yes (xlsx/csv/pdf) |
| parse_primavera_schedule | generic; chat also `primavera_parser` | construction-capable + PM/orchestrator | det | yes (.xer/.xml) |
| primavera_parse | generic (block delegate) | same | det | yes (.xer) |
| extract_quantities | generic | construction-capable | det | no (measurements list) |
| estimate_costs | generic | construction-capable | det | no (quantities/BOQ) |
| progress_tracker | generic | construction-capable | det | no |
| commissioning_checklist | own tool + generic + predispatch | construction-capable | det (templates) | optional spec |
| resource_histogram | own tool + generic + predispatch | construction-capable | det | yes (.xer) |
| claims_builder | generic + predispatch | construction-capable | det | optional schedule/contract |
| change_order_impact | generic | construction-capable; contracts-manager | det | no |
| tender_bid_analysis | generic | construction-capable | det | no |
| forensic_delay_analysis | generic | construction-capable | det | yes (two .xer) |
| warranty_maintenance_schedule | generic | construction-capable | det | no |
| submittal_log_generator | generic | construction-capable | det | no |
| risk_register_auto_populate | generic | construction-capable; safety-officer | det | no |
| procurement_optimizer | generic | construction-capable | det | no |
| procurement_analysis | generic | construction-capable | det | no |
| evm twin progress_tracker | generic | — | det | no |
| wir_form | generic + predispatch | construction-capable | det | no |
| job_requisition | generic + predispatch | construction-capable | det | no |
| safety_briefing | generic + predispatch | construction-capable | det | no |
| rfp_draft | generic + predispatch | construction-capable | det | no |
| qa_qc_inspection | generic | construction-capable | det + optional photo model | optional photos |
| process_document | generic | construction-capable | det (dispatch by type) | yes |
| process_contract | generic | construction-capable; contracts-manager | det + optional LLM extract | yes or text |
| process_specification_full | generic | construction-capable | det (spec_analyzer) | yes |
| spec_analyze | generic; own `spec_analyzer` block | construction-capable | det (block) | yes or text |
| bim_analysis | generic | construction-capable; bim-analyst | det (bim block) | yes (.ifc) |
| bim_extract | generic; own `bim_extractor` | construction-capable; bim-analyst | det | yes (.ifc) |
| bim_clash_detection | generic | construction-capable; bim-analyst | det | yes (.ifc) |
| as_built_deviation_report | generic + predispatch | construction-capable | det | optional drawings |
| carbon_footprint_calculator | generic | construction-capable | det | no (BOQ/qty) |
| safety_compliance_audit | generic | construction-capable; safety-officer | det | optional text/file |
| esg_sustainability_report | generic | construction-capable; safety-officer | det | no |
| om_manual_generator | generic + predispatch | construction-capable | det | no |
| digital_twin_sync | generic | construction-capable | NEEDS-EXTERNAL | yes / live sync |
| cde_post_rfi | generic | construction-capable | NEEDS-EXTERNAL (CDE) | no |
| cde_poll_events | generic | construction-capable | NEEDS-EXTERNAL (CDE) | no |
| intelligent_workflow | generic | construction-capable | det router | optional file |
| auto_pipeline | generic | construction-capable; PM/doc-analyst | det chain | yes |
| jetson_dispatch | generic | construction-capable | det validate; pending hardware | no |
| daily_site_report | generic | construction-capable | det + optional voice/ocr | optional |
| value_engineering | generic | construction-capable | det | no |
| track_progress | route-only (not in get_actions) | construction-capable via route | det | photos + bim |
| extract_measurements | route-only | via route | det | drawing |
| generate_construction_report | route-only | via route | det | yes |
| chat | generic | construction-capable | LLM | no |
| orchestrate | generic | construction-capable | det keyword router | no |
| learn | generic | construction-capable | det (learning_engine) | no |
| recommend | generic | construction-capable | det (recommendation_template) | no |
| benchmark_lookup | generic | construction-capable | det (historical_benchmark) | no |
| construction_calc | own tool | construction-capable | det | no — other agent |
| formula_execute | generic | construction-capable | LLM codegen — other agent | no |
| sympy_reason | generic | construction-capable | det/LLM — other agent | no |

construction-capable = the 11 agents listed above.

## Phase 2

Live chat probes need `FORK_TOKEN`. If unset, Phase 2 is blocked.
Project name if run: `FIXTURE-containers-<date>` (never master_corpus).
Conversation ids: `ctr-<action>-<epoch>`.
