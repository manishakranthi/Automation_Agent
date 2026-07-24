#!/usr/bin/env python
"""Convenience entry point: python run.py --meta meta.xlsx --pacing-sheet tracker.xlsx ..."""

from campaign_reporting_agent.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
