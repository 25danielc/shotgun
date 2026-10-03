"""Traffic-aware ETA from the Google Routes API.

Build step 5.1: coordinates -> minutes within 2 of Google Maps.

Origin is the location the CarPlay Shortcut posts to /events. Shortcuts cannot read the active
Apple Maps route, so the destination comes from a fixed home/work address, the next calendar
event, or the agent asking (open question in docs/DECISIONS.md).
Request shape checked 2026-10-03: docs/DECISIONS.md section 11 (field mask header is required).
"""
