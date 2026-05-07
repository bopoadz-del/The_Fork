# Cerebrum-Blocks / Schedule Engine / Configuration
# Anthropic 1GW Canada Data Center — L2 Schedule Parameters

project:
  name: "Anthropic 1GW Canada DC"
  start_date: "2026-05-11"
  target_b1_dh1_rfs: "2027-11-30"
  target_1gw_rfs: "2028-12-31"

schedule:
  mode: "balanced"  # options: baseline | compressed | balanced
  risk_buffer_days: 75

constraints:
  financial_close_max_days: 70
  tfo_impact_study_days: 40
  indigenous_consultation_days: 50
  generator_manufacturing_days: 120
  chiller_manufacturing_days: 90

wbs:
  - "1.0 Project Management"
  - "2.0 Transaction & Financial Close"
  - "3.0 Site Development & Entitlements"
  - "4.0 Design & Engineering"
  - "5.0 Procurement & Supply Chain"
  - "6.0 Building 1 Construction"
  - "7.0 Building 2 Construction"
  - "8.0 Building 3 Construction"
  - "9.0 Building 4 Construction"
  - "10.0 Campus Infrastructure"
  - "11.0 Project Closeout"

resources:
  categories:
    - Labor
    - MEP
    - Elect
    - IT
    - Commission
    - GC
    - PM
    - Controls
    - Security
