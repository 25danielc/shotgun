"""Food worker (hero alternative, step 3.4).

Primary: the official DoorDash CLI (waitlist-only, macOS Apple Silicon only) run as a local
worker on the Mac that polls the job table. Fallback: a browser agent that stops at a ready
cart. Never use unofficial DoorDash MCP servers.
Pass check: job -> cart with the right items and total; order placed only after approval.
Order timing: place at arrival time minus prep/delivery time (step 5.2).
"""
