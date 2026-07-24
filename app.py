#!/usr/bin/env python
"""Convenience entry point: python app.py, then open http://127.0.0.1:5000"""

from campaign_reporting_agent.webui import create_app

app = create_app()

if __name__ == "__main__":
    app.run(debug=False)
