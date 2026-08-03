# construction_advisor — prompt 1, run 1

- verdict: PASS
- prompt: for a 2.5m thick mass concrete raft, what thermal control limits apply and how long to equilibrium?
- project: dar_al_arkan_master
- conversation_id: fmx-80d02ea01c00
- route event: `{"requested": "project-assistant", "final": "project-assistant", "action": "construction_advisor", "confidence": 0.8, "reason": "below_routing_gate"}`
- tool_calls: []
- tool_results: []
- routing oracle: PASS
- execution oracle: PASS
- structure: {"has_numbers": "PASS", "shows_working_or_rule": "PASS"}
- first_token_s: 125.56  total_s: 125.86
- served_model: kimi-k2.6  fallback_used: False
- answer_chars: 1536  sources: 1  events: 31

## Answer (verbatim)

Based on the reference context, here are the thermal control limits that apply to the mass-concrete raft:

**Thermal control limits**
- The temperature difference between the core of the raft and the surface of the concrete must not exceed **20 °C**.
- The core temperature must not exceed **70 °C**.  
*(source: SMGT-C552-part-1.pdf, chunk 40)*

The document expresses these as:  
*Tc – Ts ≤ 20 °C* and *core temperature ≤ 70 °C*.

**Time to temperature equilibrium (Tc = Ts) for a 2.5 m thick raft**
The reference context **does not explicitly state** the equilibrium time for a **2.5 m** thick slab.  
*(source: SMGT-C552-part-1.pdf, chunk 59)*

What the context does provide are two calculated examples using the formula it presents:
- A **1.2 m** thick slab reaches temperature balance in **107.52 hours**.
- A **150 m** thick dam wall reaches temperature balance in **10,000 weeks** (≈ **192 years**).  
*(source: SMGT-C552-part-1.pdf, chunk 59)*

**Other thermal-control requirements from the same reference**
- Before casting, a model cube of **4.0 m × 4.0 m × 4.0 m** high must be made on site.
- The shutter must be isolated from the outside.
- The top surface must be insulated with insulation boards and polythene sheets.
- Thermocouples are required at **300 mm** intervals horizontally and vertically.
- Temperature readings must be taken every **1 hour** for a minimum period of **7 days**.
- Maximum core temperature is usually reached **35 – 40 hours** after casting.  
*(sources: SMGT-C552-part-1.pdf, chunks 40 & 59)*
