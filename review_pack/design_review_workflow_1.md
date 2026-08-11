# design_review_workflow — prompt 1, run 1

- verdict: PASS
- prompt: what statuses can a design review package come back with under PRC-501?
- project: master_corpus
- conversation_id: fmx-997d75e4df3a
- route event: `{"requested": "project-assistant", "final": "project-assistant", "action": "design_review_workflow", "confidence": 1.0, "reason": "below_routing_gate"}`
- tool_calls: []
- tool_results: []
- routing oracle: PASS
- execution oracle: PASS
- structure: {}
- first_token_s: 30.38  total_s: 30.64
- served_model: kimi-k2.6  fallback_used: False
- answer_chars: 818  sources: 3  events: 15

## Answer (verbatim)

Under PRC-501, a design review package may only carry one of these two valid statuses:

* **`FOR_COMMENT`** — issued for review and feedback  
* **`ACCEPTANCE`** — accepted for use without further comment

The following statuses are explicitly prohibited and must never appear on design submissions:

* **`APPROVED`**
* **`APPROVAL`**
* **`SIGN_OFF`**

A QA officer or Lead Engineer who stamps a drawing with any of the prohibited terms is non-compliant with PRC-501; the document must be re-stamped with one of the two valid statuses. *(Source: PRC-501 — Design Reviews & Acceptance, doc\_id=25742a44, chunks 0, 2, 3)*

> **Standards note (advisory — flagging, not blocking):**
> - **PRC-501** — The word 'APPROVED' is contractually prohibited on design documents. Use 'accepted', 'for comment', or 'buy-off' instead.
