"""
Block: cpm_core
Role: Critical Path Method engine — forward pass, backward pass, float calculation
Interface: run_cpm(df, successors, project_start) -> df with ES, EF, LS, LF, TF, FF, Critical
"""
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List

def run_cpm(df, successors: Dict[int, List[int]], project_start: datetime):
    """
    Execute full CPM algorithm on activity network.
    Mutates df in-place with ES, EF, LS, LF, Total_Float, Free_Float, Critical, Start_Date, Finish_Date.
    Returns project_duration (days).
    """
    n = len(df)

    # Forward pass
    es = np.zeros(n, dtype=int)
    ef = np.zeros(n, dtype=int)

    for i in range(n):
        preds = df.at[i, 'Predecessors']
        if not preds:
            es[i] = 0
        else:
            es[i] = max(ef[df[df['ID'] == p].index[0]] for p in preds)
        ef[i] = es[i] + df.at[i, 'Duration']

    project_duration = int(max(ef))

    # Backward pass
    ls = np.full(n, project_duration, dtype=int)
    lf = np.full(n, project_duration, dtype=int)

    for i in range(n):
        ls[i] = project_duration - df.at[i, 'Duration']

    for _ in range(n):
        changed = False
        for i in range(n-1, -1, -1):
            id_val = df.at[i, 'ID']
            succs = successors[id_val]
            if succs:
                new_lf = min(ls[df[df['ID'] == s].index[0]] for s in succs)
            else:
                new_lf = project_duration
            new_ls = new_lf - df.at[i, 'Duration']
            if lf[i] != new_lf:
                lf[i] = new_lf
                ls[i] = new_ls
                changed = True
        if not changed:
            break

    # Floats
    total_float = ls - es
    free_float = np.zeros(n, dtype=int)
    for i in range(n):
        id_val = df.at[i, 'ID']
        succs = successors[id_val]
        if succs:
            min_es_succ = min(es[df[df['ID'] == s].index[0]] for s in succs)
            free_float[i] = min_es_succ - ef[i]
        else:
            free_float[i] = project_duration - ef[i]

    critical = (total_float == 0).astype(int)

    # Attach to DataFrame
    df['ES'] = es
    df['EF'] = ef
    df['LS'] = ls
    df['LF'] = lf
    df['Total_Float'] = total_float
    df['Free_Float'] = free_float
    df['Critical'] = critical
    df['Start_Date'] = [project_start + timedelta(days=int(d)) for d in es]
    df['Finish_Date'] = [project_start + timedelta(days=int(d)) for d in ef]

    return project_duration

def get_critical_path(df) -> pd.DataFrame:
    """Return only critical activities, sorted by Early Start."""
    return df[df['Critical'] == 1].sort_values('ES').copy()

def get_milestones(df) -> pd.DataFrame:
    """Return zero-duration milestone activities."""
    return df[df['Duration'] == 0].copy()
