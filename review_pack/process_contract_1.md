# process_contract — prompt 1, run 1

- verdict: PASS
- prompt: review the contract - what do the FIDIC clauses say about delay damages?
- project: dar_al_arkan_master
- conversation_id: fmx-38b5840fc563
- route event: `{"requested": "project-assistant", "final": "project-assistant", "action": "process_contract", "confidence": 0.4, "reason": "below_routing_gate"}`
- tool_calls: []
- tool_results: []
- routing oracle: PASS
- execution oracle: PASS
- structure: {}
- first_token_s: 12.75  total_s: 12.92
- served_model: glm-5.2:cloud  fallback_used: False
- answer_chars: 2031  sources: 3  events: 29

## Answer (verbatim)

## Delay Damages under FIDIC — from the project reference context

The retrieved FIDIC contract administration reference provides the following on delay damages:

### Clause 8 — Programme & Time

| Item | Rule |
|---|---|
| **Delay Damages** | Pre-agreed rate; **capped** in Contract Data → hitting the cap can trigger termination (SC 15.2) |

*(source: FIDIC 2017 Contract Administration — Practice Layer, chunk 9)*

### Clause 15.2 — Employer Termination

The reference notes that under the 2017 edition, **exceeding the delay-damages cap** was added as a new ground for Employer termination. The full list of Employer termination grounds under SC 15.2 is:

- Failure to correct/perform
- Abandonment
- No-consent whole-Works subcontract
- Insolvency
- Bribery
- **2017 additions:** ignoring a binding DAAB decision; **exceeding the delay-damages cap**; JV-member default

The reference also advises that a **Notice to Correct (15.1)** should preferably be issued first — termination is described as "the nuclear option."

*(source: FIDIC 2017 Contract Administration — Practice Layer, chunk 16)*

### Key takeaways

1. **Delay damages are a pre-agreed rate** — set in the Contract Data, not determined after the fact.
2. **They are capped** — the Contract Data specifies a maximum cumulative amount.
3. **Hitting the cap has consequences beyond money** — it can trigger Employer termination under SC 15.2 (a 2017 addition).
4. The Programme is the **baseline for delay analysis** — an un-updated programme undermines an EOT claim, which is the Contractor's primary defence against delay damages.

*(source: FIDIC 2017 Contract Administration — Practice Layer, chunks 9, 10, 16)*

---

**What I don't have from this context:** The specific pre-agreed delay-damages rate or cap amount for *this* project — those would be in the project's Contract Data / Particular Conditions, which are not in the retrieved chunks. If you need the actual figures, I can search the project documents for the contract data or particular conditions.
