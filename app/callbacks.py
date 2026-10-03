"""Callback watcher: when a job reaches done or needs_approval, ring the car with a summary.

Build steps: 4.1 (job flipped to done by hand -> phone rings and reads the summary),
4.2 (spoken approval loop).

Runs as a background loop in the FastAPI process (Railway keeps the container running; see
docs/DECISIONS.md on why not Cloud Run). Marks a job as announced so it never rings twice.
Calls go through app/telephony.py with the summary passed as a dynamic variable.
"""
