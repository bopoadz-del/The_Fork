# job_requisition — prompt 1, run 1

- verdict: PASS
- prompt: start a job requisition and prequalification shortlist for the ELV package
- project: dar_al_arkan_master
- conversation_id: fmx-d98672d12220
- route event: `{"requested": "project-assistant", "final": "project-assistant", "action": "job_requisition", "confidence": 0.6, "reason": "below_routing_gate"}`
- tool_calls: []
- tool_results: []
- routing oracle: PASS
- execution oracle: PASS
- structure: {}
- first_token_s: 83.05  total_s: 83.36
- served_model: kimi-k2.6  fallback_used: False
- answer_chars: 1843  sources: 3  events: 32

## Answer (verbatim)

The provided context establishes the pre-qualification workflow that would apply to the ELV procurement package, but it does **not** contain ELV-specific scope details, vendor lists, or document templates needed to draft the actual job requisition and shortlist.

**Pre-qualification process (from the Project Execution Plan)**
The procedure for any procurement package—including ELV—is as follows:

1. **Tailor the Pre-Qualification Questionnaire (PQQ)** specifically for the ELV procurement package.  
2. **Obtain DGCL approval** before issue.  
3. **Issue the PQQ** to all vendors who have completed NDAs and submitted their SOIs.  
4. **Assess received PQQs** and compile a shortlist.  
5. **Issue the shortlist to the client** for approval.  
*(Source: the project Project Execution Plan, chunk 553; 054 – the project Infrastructure #1 – Project Execution Plan – CAW, chunk 378)*

**Schedule context**
The project baseline schedule shows the activity *“Approval of Supplier Prequalification”* as a 15-day work item (29-Apr-24 to 15-May-24), followed by material submission, material approval, and purchase-order issue.  
*(Source: 054-Project Baseline Schedule, chunk 669)*

**What is missing from this context**
- ELV package scope, technical specifications, or bill of materials  
- List of vendors who have completed NDAs/SOIs for the ELV package  
- A job-requisition template or pre-qualification shortlist format  
- Current status of any ELV vendor submissions  

To proceed, you would need to upload or reference the ELV package scope and vendor register so the PQQ can be tailored and the shortlist compiled per the process above.

> **Standards note (advisory — flagging, not blocking):**
> - **PRC-501** — The word 'APPROVED' is contractually prohibited on design documents. Use 'accepted', 'for comment', or 'buy-off' instead.
