"""
Block: resource_histogram
Role: Calculate weekly manpower loading by trade category
Interface: build_histogram(df, project_start, project_duration) -> DataFrame with weekly headcounts
"""
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import List, Dict

def build_histogram(df, project_start: datetime, project_duration: int, 
                    resource_categories: List[str] = None) -> pd.DataFrame:
    """
    Build weekly manpower histogram from activity resource assignments.
    Returns DataFrame with Week_Start, Week_End, and headcount per category + Total.
    """
    if resource_categories is None:
        resource_categories = ['Labor', 'MEP', 'Elect', 'IT', 'Commission', 'GC', 'PM', 'Controls', 'Security']

    num_weeks = (project_duration // 7) + 2
    week_starts = [project_start + timedelta(days=7*w) for w in range(num_weeks)]
    week_ends = [ws + timedelta(days=6) for ws in week_starts]

    hist_matrix = {cat: np.zeros(num_weeks, dtype=int) for cat in resource_categories}

    for i in range(len(df)):
        act_start = df.at[i, 'Start_Date']
        act_finish = df.at[i, 'Finish_Date']
        resources = df.at[i, 'Resources']

        for w in range(num_weeks):
            ws = week_starts[w]
            we = week_ends[w]
            if act_start <= we and act_finish >= ws:
                for cat in resource_categories:
                    if cat in resources:
                        hist_matrix[cat][w] += resources[cat]

    hist_df = pd.DataFrame({'Week_Start': week_starts, 'Week_End': week_ends})
    for cat in resource_categories:
        hist_df[cat] = hist_matrix[cat]
    hist_df['Total'] = hist_df[resource_categories].sum(axis=1)

    return hist_df

def get_peak_week(hist_df: pd.DataFrame) -> dict:
    """Return peak manpower week and values."""
    peak_idx = hist_df['Total'].idxmax()
    row = hist_df.iloc[peak_idx]
    return {
        'week_number': peak_idx + 1,
        'week_start': row['Week_Start'].strftime('%Y-%m-%d'),
        'total': int(row['Total']),
        'labor': int(row['Labor']),
        'mep': int(row['MEP']),
        'elect': int(row['Elect']),
        'it': int(row['IT'])
    }
