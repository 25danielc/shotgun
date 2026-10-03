"""Railway build/deploy config stays consistent (step 1.4).

Railpack fails at build time ("No start command detected") unless railpack.json names the start
command, because our app lives at app/main.py, not main.py. railway.json repeats it for the
deploy. They must agree.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_start_commands_agree_and_point_at_the_app():
    railpack = json.loads((ROOT / "railpack.json").read_text())["deploy"]["startCommand"]
    railway = json.loads((ROOT / "railway.json").read_text())["deploy"]["startCommand"]
    assert railpack == railway
    assert "app.main:app" in railpack
    assert "${PORT" in railpack and "0.0.0.0" in railpack


def test_healthcheck_matches_the_health_route():
    railway = json.loads((ROOT / "railway.json").read_text())
    assert railway["deploy"]["healthcheckPath"] == "/health"
