# handover_management — prompt 1, run 1

- verdict: PASS
- prompt: what are the prerequisites for practical completion and the DLP obligations?
- project: dar_al_arkan_master
- conversation_id: fmx-cab4de8aa686
- route event: `{"requested": "project-assistant", "final": "project-assistant", "action": "handover_management", "confidence": 0.6, "reason": "below_routing_gate"}`
- tool_calls: []
- tool_results: []
- routing oracle: PASS
- execution oracle: PASS
- structure: {}
- first_token_s: 15.55  total_s: 15.72
- served_model: glm-5.2:cloud  fallback_used: False
- answer_chars: 3367  sources: 3  events: 46

## Answer (verbatim)

## Prerequisites for Practical Completion (Taking-Over) and DLP Obligations

Based on the reference context, here is what the project documents specify:

### Prerequisites for Taking-Over (Practical Completion)

1. **Substantial completion** — The Taking-Over Certificate (TOC) under **SC 10.1** is issued when the works are *substantially* complete. *(source: chunk 11, doc 41227807)*

2. **Tests on Completion must be addressed** — Under **SC 9.4**, if the works fail Tests on Completion, the Engineer has three options:
   - Accept with reduced performance (leading to Performance Damages / price reduction),
   - Reject, or
   - Re-test (with no repeat limit).
   
   The works cannot be taken over until the Tests on Completion are resolved. *(source: chunk 10, doc 41227807)*

3. **Engineer's 28-day window** — Once the works are substantially complete, the Engineer has **28 days** to issue the TOC; if the Engineer does not act within that period, the TOC is **deemed issued**. *(source: chunk 11, doc 41227807)*

4. **Early use / partial taking-over (Clauses 10.2 / 10.3)** — If the Employer uses any part of the works before formal taking-over, that part is **deemed taken over**. Outstanding tests for that part can then run into the Defects Notification Period (DNP). This is illustrated by the WWTP scenario, where the Engineer may issue a TOC at the end of the Time for Completion with tests deferred into the DNP under clause 10.3. *(source: chunks 11 and 22, doc 41227807)*

### Effects of Taking-Over

Upon issuance (or deemed issuance) of the TOC:
- **Care of the works transfers to the Employer**;
- **The Defects Notification Period (DNP) starts**;
- **50% of retention is released**;
- The **Date of Completion** is set.

*(source: chunk 11, doc 41227807)*

### DNP (Defects Notification Period) Obligations

- **Duration:** The DNP is **typically 12 months from the Taking-Over Certificate**, and is **extendable** (the chunk cuts off before specifying the upper limit or mechanism). *(source: chunk 11, doc 41227807)*
- **Outstanding tests in the DNP:** Where tests on completion could not be completed before taking-over (e.g., no inflow at a WWTP), they may be carried out during the DNP under clause 10.3, with the contract providing for this scenario. *(source: chunk 22, doc 41227807)*
- **Final payment:** The contract flow includes a **Statement at Completion** and a **Final Statement / Final Payment Certificate**, which are tied to completion of the DNP and resolution of defects. *(source: chunk 8, doc 55502db6)*

### Claims and Disputes During the DNP

- Any claim arising during the DNP must follow **Clause 20**: a party must give **Notice of Claim within 28 days** of the event; failure to do so can **time-bar** the claim. *(source: chunk 8, doc 55502db6)*
- Disputes are referred to a standing **DAAB (Dispute Avoidance/Adjudication Board)** under **Clause 21**; its decision is binding unless/until revised by arbitration. *(source: chunk 8, doc 55502db6)*

---

**Note:** The reference context cuts off mid-sentence on the DNP extension provision ("extendable to a…"), so I do not have the full details on the maximum extension period or the specific mechanism for extension. If you need that detail, the source document (likely the Particular Conditions or General Conditions commentary) would need to be consulted further.
