# Cerebrum-Blocks / Schedule Engine

Modular L2 Management Summary Schedule (SMS) generator for construction projects.
Built for Anthropic 1GW Canada Data Center RFP response.

## Architecture (Lego Blocks)

| Block | File | Role |
|-------|------|------|
| Config | `config.yaml` | Project dates, targets, constraints |
| Loader | `activity_loader.py` | Ingest activities, validate network |
| CPM Core | `cpm_core.py` | Forward/backward pass, float, critical path |
| Optimizer | `fast_track_optimizer.py` | Duration compression, buffer injection |
| Histogram | `resource_histogram.py` | Weekly manpower by trade |
| Exporter | `excel_exporter.py` | Excel with Gantt, charts, analysis |
| Orchestrator | `main.py` | CLI entry point, wires all blocks |

## Quick Start

```bash
pip install pandas numpy openpyxl pyyaml
python blocks/schedule_engine/main.py --mode balanced --output Anthropic_L2.xlsx
```

## Modes

- `baseline` — Conservative durations, no fast-track
- `compressed` — Maximum compression, August 2027 B1 RFS
- `balanced` — Realistic with 75-day risk buffer, November 2027 B1 RFS

## CPM Algorithm

Manual implementation (no MS Project / Primavera dependency):
1. Forward pass: ES = max(EF of predecessors), EF = ES + Duration
2. Backward pass: LF = min(LS of successors), LS = LF - Duration
3. Total Float = LS - ES
4. Critical = TF == 0

## Output

5-sheet Excel:
1. L2 Schedule — 250 activities with monthly Gantt bars
2. Critical Path — 49 driving activities
3. Manpower Histogram — Weekly headcount with bar charts
4. WBS Dictionary — 11 work breakdown elements
5. Schedule Analysis — Float report, recommendations
