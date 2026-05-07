#!/usr/bin/env python3
"""
Cerebrum-Blocks / Schedule Engine / Main Orchestrator
Usage: python main.py --mode balanced --output schedule.xlsx
"""
import argparse
import yaml
from datetime import datetime
from activity_loader import load_activities, build_successor_map, validate_network
from cpm_core import run_cpm, get_critical_path, get_milestones
from resource_histogram import build_histogram, get_peak_week
from excel_exporter import export_schedule
from fast_track_optimizer import optimize

def main():
    parser = argparse.ArgumentParser(description='L2 Schedule Engine')
    parser.add_argument('--mode', choices=['baseline', 'compressed', 'balanced'], default='balanced')
    parser.add_argument('--config', default='config.yaml')
    parser.add_argument('--output', default='L2_Schedule.xlsx')
    args = parser.parse_args()

    # Load config
    with open(args.config, 'r') as f:
        config = yaml.safe_load(f)

    project_start = datetime.strptime(config['project']['start_date'], '%Y-%m-%d')
    target_rfs = datetime.strptime(config['project']['target_b1_dh1_rfs'], '%Y-%m-%d')
    risk_buffer = config['schedule']['risk_buffer_days']

    # Load activities
    df = load_activities(mode=args.mode)
    validate_network(df)
    successors = build_successor_map(df)

    # Optimize if balanced mode
    if args.mode == 'balanced':
        df = optimize(df, target_rfs, project_start, risk_buffer)
        # Rebuild successors after potential insertions
        successors = build_successor_map(df)

    # Run CPM
    project_duration = run_cpm(df, successors, project_start)

    # Build histogram
    hist_df = build_histogram(df, project_start, project_duration)
    peak = get_peak_week(hist_df)

    # Export
    export_schedule(df, hist_df, project_start, project_duration, args.output)

    # Console report
    milestones = get_milestones(df)
    b1_rfs = milestones[milestones['Name'].str.contains('B1 DH1 RFS', na=False)]
    final = milestones[milestones['Name'].str.contains('1 GW Campus', na=False)]

    print(f"\n✅ Schedule exported: {args.output}")
    print(f"   Mode: {args.mode}")
    print(f"   Activities: {len(df)}")
    print(f"   Duration: {project_duration} days")
    print(f"   B1 DH1 RFS: {b1_rfs['Finish_Date'].values[0] if len(b1_rfs) > 0 else 'N/A'}")
    print(f"   1 GW RFS: {final['Finish_Date'].values[0] if len(final) > 0 else 'N/A'}")
    print(f"   Peak manpower: {peak['total']} (week {peak['week_number']})")

if __name__ == '__main__':
    main()
