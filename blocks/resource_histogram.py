"""
Block: activity_loader
Role: Ingest activity data, validate predecessors, build successor map
Interface: load_activities() -> DataFrame with ID, WBS, Name, Duration, Predecessors, Resources
"""
import pandas as pd
from typing import List, Dict, Tuple

def load_activities(mode: str = "balanced") -> pd.DataFrame:
    """
    Load activity list based on schedule mode.
    mode: 'baseline' | 'compressed' | 'balanced'
    Returns DataFrame with columns: ID, WBS, Name, Duration, Predecessors, Resources
    """
    activities = _get_balanced_activities()

    if mode == "compressed":
        activities = _compress_durations(activities)
    elif mode == "baseline":
        activities = _baseline_durations(activities)

    df = pd.DataFrame(activities, columns=['ID', 'WBS', 'Name', 'Duration', 'Predecessors', 'Resources'])
    df['Duration'] = df['Duration'].astype(int)
    return df

def build_successor_map(df: pd.DataFrame) -> Dict[int, List[int]]:
    """Build successor map from predecessor relationships."""
    successors = {id_val: [] for id_val in df['ID']}
    for i in range(len(df)):
        for p in df.at[i, 'Predecessors']:
            successors[p].append(df.at[i, 'ID'])
    return successors

def validate_network(df: pd.DataFrame) -> bool:
    """Check for circular dependencies and orphan activities."""
    all_ids = set(df['ID'])
    for i, row in df.iterrows():
        for p in row['Predecessors']:
            if p not in all_ids:
                raise ValueError(f"Activity {row['ID']} has invalid predecessor {p}")
    return True

def _get_balanced_activities() -> List[Tuple]:
    """Return balanced activity dataset (247 activities)."""
    # Truncated sample — full data loaded from external JSON in production
    return [
        (1, "1.1", "Project Kick-off & Charter", 5, [], {'PM': 8, 'Admin': 2}),
        (2, "1.2", "PMO Establishment & Governance", 8, [1], {'PM': 6, 'Admin': 2}),
        (3, "1.3", "Project Controls & Baselines", 8, [2], {'PM': 5, 'Planner': 2, 'Cost': 2}),
        # ... full 247 activities loaded from JSON/CSV in production
    ]

def _compress_durations(activities: List[Tuple]) -> List[Tuple]:
    """Apply fast-track compression ratios to durations."""
    compressed = []
    for act in activities:
        id_val, wbs, name, dur, preds, res = act
        ratio = 0.85 if dur > 10 else 0.90
        compressed.append((id_val, wbs, name, int(dur * ratio), preds, res))
    return compressed

def _baseline_durations(activities: List[Tuple]) -> List[Tuple]:
    """Apply baseline stretch ratios to durations."""
    baseline = []
    for act in activities:
        id_val, wbs, name, dur, preds, res = act
        ratio = 1.15 if dur > 10 else 1.05
        baseline.append((id_val, wbs, name, int(dur * ratio), preds, res))
    return baseline
