"""Worker adapters. Each worker claims jobs of one type from the job table (app/jobs.py).

Pattern: claim a job, do the work, settle it to done / needs_approval / failed
(see app/workers/coder.py).
"""
