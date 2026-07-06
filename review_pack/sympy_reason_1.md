# sympy_reason — prompt 1, run 1

- verdict: PASS
- prompt: run a variance analysis - planned SAR 4.2M vs actual SAR 5.1M, what drove the 21% overrun?
- project: dar_al_arkan_master
- conversation_id: fmx-2a0832bba209
- route event: `{"requested": "project-assistant", "final": "heavy-reasoning", "action": "sympy_reason", "confidence": 0.4, "reason": "needs_planning"}`
- tool_calls: [{"name": "sympy_reasoning", "args": "{\"input\":\"{\\\"planned_cost\\\": 4200000, \\\"actual_cost\\\": 5100000, \\\"currency\\\": \\\"SAR\\\", \\\"description\\\": \\\"Cost variance analysis: planned SAR 4.2M vs actual SAR 5.1M\\\"}\"}", "id": null}, {"name": "recommendation_template", "args": "{\"input\":\"{\\\"planned_cost\\\": 4200000, \\\"actual_cost\\\": 5100000, \\\"variance_pct\\\": 21.43, \\\"currency\\\": \\\"SAR\\\", \\\"context\\\": \\\"DG II Infrastructure Package 1 \u2014 cumulative variance worsening from -13.3", "id": null}, {"name": "search_project_documents", "args": "{\"query\":\"cost overrun variance SAR budget actual expenditure BOQ\",\"top_k\":5}", "id": null}, {"name": "formula_executor_v2", "args": "{\"input\":\"{\\\"expression\\\": \\\"actual_cost - planned_cost\\\", \\\"variables\\\": {\\\"planned_cost\\\": 4200000, \\\"actual_cost\\\": 5100000}, \\\"units\\\": {\\\"planned_cost\\\": \\\"SAR\\\", \\\"actual_cost\\\": \\\"SAR\\\"}}\"}", "id": null}, {"name": "formula_executor_v2", "args": "{\"input\":\"{\\\"expression\\\": \\\"(actual_cost - planned_cost) / planned_cost * 100\\\", \\\"variables\\\": {\\\"planned_cost\\\": 4200000, \\\"actual_cost\\\": 5100000}, \\\"units\\\": {\\\"planned_cost\\\": \\\"SAR\\\", \\\"actual_", "id": null}]
- tool_results: [{"name": "sympy_reasoning", "ok": true, "duration_ms": 0, "error": null}, {"name": "recommendation_template", "ok": true, "duration_ms": 0, "error": null}, {"name": "search_project_documents", "ok": true, "duration_ms": 0, "error": null}, {"name": "formula_executor_v2", "ok": true, "duration_ms": 593, "error": null}, {"name": "formula_executor_v2", "ok": true, "duration_ms": 390, "error": null}]
- routing oracle: PASS
- execution oracle: PASS
- structure: {"has_numbers": "PASS", "shows_working_or_rule": "PASS"}
- first_token_s: 35.58  total_s: 35.66
- served_model: glm-5.2:cloud  fallback_used: False
- answer_chars: 4128  sources: 0  events: 67

## Answer (verbatim)

## Variance Analysis: Planned SAR 4.2M vs Actual SAR 5.1M

### Headline Finding

```
Finding: SAR 900,000 cost overrun (21.43% above plan) on DG II Infrastructure Package 1
- Source: formula_executor_v2 (cost delta + variance %)
- Math: (5,100,000 − 4,200,000) / 4,200,000 × 100
- Result: SAR 900,000 overrun | 21.43% variance
- Validation: syntactic ✓ | dimensional ✓ | physical ✓ | empirical ✓ | operational ✓
- Confidence: High (arithmetic); Medium (root-cause attribution — see below)
```

### What the Reference Context Tells Us About Drivers

The corpus does **not** contain a line-item cost breakdown that decomposes the SAR 900,000 into specific BOQ items. However, the retrieved documents establish three corroborating indicators that frame the overrun:

| # | Evidence from Corpus | Implication for Cost Overrun |
|---|---|---|
| 1 | **Cumulative schedule variance worsened from −13.33% (Week 35) to −15.89% (Week 37)** — a 2.56 pp deterioration in two weeks | Schedule slippage drives extended preliminaries, standing costs, and potential rework — direct cost pressure |
| 2 | **Forecast finish slipped from 17-Sep-26 to 30-Sep-26** (+13 days in two reporting cycles); planned finish remains 10-May-26 | Time-over-time cost escalation; the gap between planned and forecast is widening, not narrowing |
| 3 | **"BoQ-vs-General-Conditions clashes are a classic dispute"** (SC 12.3: measure net actual quantity of permanent works) | Measurement/remasurement disputes may be inflating valued quantities beyond BoQ allowances — a structural cost driver |

### What I Cannot Confirm from the Reference Context

- **No unit-rate or quantity variance data** is present in the retrieved chunks. I cannot decompose the SAR 900,000 into, e.g., concrete volume overrun, rebar rate escalation, or provisional sum drawdown.
- **No change order or claim register** was retrieved. The overrun may include approved variations, but the corpus doesn't confirm this.
- **No procurement or material price escalation data** appears in the top matches.

I will not fabricate a line-item attribution. The root-cause decomposition requires the BOQ-to-actual valuation comparison, which is not in the provided context.

---

### Recommendation

```
Recommendation: Decompose the SAR 900,000 overrun into BOQ line-item variances using SC 12.3 (measure net actual quantity) — to isolate measurement/remeasurement disputes from schedule-driven preliminaries and approved variations.
- Severity: High (21.43% exceeds the 8% action threshold by 2.7×)
- Cost impact: SAR 900,000 (confirmed); potential further exposure if forecast slip continues
- Time impact: +13 days forecast slip already observed (17-Sep-26 → 30-Sep-26); planned 10-May-26
- Owner: QS (measurement/valuation) | PM (schedule recovery) | Contracts (variation/EOT assessment)
```

**Immediate actions:**

1. **QS** — Pull the BOQ and compare priced quantities against measured quantities per SC 12.3. Flag any line item where actual measured quantity exceeds BoQ allowance by ≥8%. The corpus explicitly warns that "BoQ-vs-General-Conditions clashes" on lump-sum/provisional items against re-measurement rules are a classic dispute source.

2. **PM** — The programme is the baseline for delay analysis. The un-updated programme undermines EOT claims. The widening cumulative variance (−13.33% → −15.89%) and forecast slip (+13 days in two weeks) indicate the recovery plan is not working. Re-baseline or update the programme now.

3. **Contracts** — Issue or compile a variation register. Determine what portion of the SAR 900,000 is approved variations vs. unapproved scope creep vs. rate/quantity remeasurement. Without this split, the overrun cannot be managed or recovered.

---

**Bottom line:** The 21.43% overrun (SAR 900,000) is confirmed arithmetically. The corpus provides strong circumstantial evidence that schedule deterioration and measurement/remeasurement disputes are contributing, but a definitive line-item root cause requires the BOQ-to-actual valuation data, which is not in the retrieved context. I recommend requesting that data to complete the decomposition.
