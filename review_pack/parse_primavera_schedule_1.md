# parse_primavera_schedule — prompt 1, run 1

- verdict: PARTIAL
- prompt: extract the key milestones from the project programme with their dates
- project: ff905e29
- conversation_id: fmx-2486a8a88840
- route event: `{"requested": "project-assistant", "final": "heavy-reasoning", "action": "parse_primavera_schedule", "confidence": 0.2, "reason": "needs_planning"}`
- tool_calls: []
- tool_results: []
- routing oracle: PASS
- execution oracle: PASS
- structure: {"milestones_have_dates": "FAIL"}
- first_token_s: 2.5  total_s: 2.5
- served_model: meta-llama/llama-4-scout-17b-16e-instruct  fallback_used: False
- answer_chars: 2020  sources: 3  events: 29

## Answer (verbatim)

## Key Milestones from the Project Programme


| Event | Deadline | Owner |
|---|---|---|
| Deliver Performance Security | ≤ 28 days after Contract Agreement | Contractor |
| Initial Programme | ≤ 28 days after Commencement | Contractor |
| Engineer review — initial Programme | 21 days | Engineer |
| Engineer review — revised Programme | 14 days | Engineer |
| Notice of Claim | ≤ 28 days from awareness | Claiming party |
| Fully detailed claim | ≤ 84 days from event | Claiming party |
| Issue IPC | ≤ 28 days of Statement | Engineer |
| Pay certified amount | ≤ 56 days of IPC | Employer |
| Issue Taking-Over Certificate | ≤ 28 days of application (else deemed) | Engineer |
| Performance Certificate | ≤ 28 days after last DNP expiry | Engineer |
| Return Performance Security | ≤ 28 days after Performance Certificate | Employer |
| DAAB decision | ≤ 84 days from referral | DAAB |


## Programme & Time (Clause 8)


| Item | Rule |
|---|---|
| Initial detailed Programme | Submit **within 28 days** of Commencement (SC 8.3) |
| Engineer review of initial Programme | **21 days** → silence = deemed No-objection (SC 1.1.66) |
| Engineer review of a revised Programme | **14 days** → silence = deemed accepted |
| EOT grounds (SC 8.4) | Variation/qty change; delay giving Cost entitlement; exceptionally adverse climate; Unforeseeable shortages (epidemic/govt); any Employer-attributable delay |
| EOT notice | **28 days** from awareness (SC 20.1) or risk **time-bar** |
| Delay Damages | Pre-agreed rate; **capped** in Contract Data → hitting the cap can trigger termination (SC 15.2) |


## Claims Procedure (Clause 20)


| Step | Deadline |
|---|---|
| **Notice of Claim** | **28 days** from awareness — miss it and the claim is **time-barred** |
| Fully detailed claim | **84 days** from the event (interim detailed claims every 28 days if the event is continuing) |
| Engineer initial response | 14 days |
| Engineer agreement/determination | proceed under SC 3.7; determine within **84 days** of the claim |
