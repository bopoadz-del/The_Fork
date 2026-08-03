# document_metadata — prompt 1, run 1

- verdict: PARTIAL
- prompt: list the documents in this project and what type each one is
- project: 64362c65
- conversation_id: fmx-c47e13a992a5
- route event: `{"requested": "project-assistant", "final": "project-assistant", "action": "process_document", "confidence": 1.0, "reason": "below_routing_gate"}`
- tool_calls: []
- tool_results: []
- routing oracle: PASS
- execution oracle: PASS
- structure: {"lists_documents": "FAIL"}
- first_token_s: 106.3  total_s: 106.55
- served_model: kimi-k2.6  fallback_used: False
- answer_chars: 1452  sources: 0  events: 29

## Answer (verbatim)

The reference context lists five document entries from the project’s **Docs** sheet. It does **not** specify a document “type” field (e.g., BOQ, specification, drawing, RFI, etc.) for these entries, so I cannot state a type for each one. The entries provided are:

1. **Document No:** IP-LND-061-0000-BNH-WIR-QL-000365 | **Rev:** 00 | **Source:** WF-001935 | **Title:** Removal of type A Hoarding beside gate D | **Review Status:** Approved  
2. **Document No:** IP-LND-061-0000-BNH-WIR-QL-000146 | **Rev:** 01 | **Source:** WF-001104 | **Title:** Installation of type A Hoarding beside gate D | **Review Status:** Approved  
3. **Document No:** IP-LND-061-0000-BNH-WIR-QL-000153 | **Rev:** 00 | **Source:** WF-000678 | **Title:** Installation of type B hoarding around temporary laydown area | **Review Status:** Approved  
4. **Document No:** IP-LND-061-0000-BNH-WIR-QL-000243 | **Rev:** 01 | **Source:** WF-001381 | **Title:** Disposal materials quantity | **Review Status:** Approved  
5. **Document No:** IP-LND-061-0000-BNH-WIR-QL-000238 | **Rev:** 03 | **Source:** WF-004033 | **Title:** Disposal materials quantity from date 8/1/2023 up to date 4/3/2023 | **Review Status:** Approved  

*(Source: doc_id=e15049a4bab3, chunks 90, 23, 26, 40, 38)*

> **Standards note (advisory — flagging, not blocking):**
> - **PRC-501** — The word 'APPROVED' is contractually prohibited on design documents. Use 'accepted', 'for comment', or 'buy-off' instead.
