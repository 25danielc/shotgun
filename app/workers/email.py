"""Email worker via Composio Gmail tools on a throwaway Gmail account (stretch, step 3.2).

Pass check: job -> draft in Gmail, state needs_approval; approve -> sent.
Sending is irreversible: draft first, send only after approve_action.
Packages and Gmail slugs checked 2026-10-03: docs/DECISIONS.md section 11.
"""
