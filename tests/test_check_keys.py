"""Offline tests for scripts/check_keys.py: missing keys FAIL and secrets never leak."""

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "check_keys", Path(__file__).resolve().parents[1] / "scripts" / "check_keys.py"
)
check_keys = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_keys)


def test_missing_keys_fail_without_network():
    checks = [("Anthropic", ["ANTHROPIC_API_KEY"], lambda env: 1 / 0)]
    assert check_keys.run({}, checks) == [
        ("Anthropic", "FAIL", "missing ANTHROPIC_API_KEY in .env")
    ]


def test_secret_values_are_scrubbed_from_failures():
    secret = "super-secret-value-123"  # secret-scan: allow (fake)

    def leaky(env):
        raise RuntimeError(f"bad key {secret}")

    [(_, status, detail)] = check_keys.run({"K": secret}, [("Leaky", ["K"], leaky)])
    assert status == "FAIL"
    assert secret not in detail
    assert "***" in detail


def test_local_secret_and_phone_checks():
    env = {"S": "short", "P": "+17345551234", "Q": "734-555-1234"}
    checks = [
        ("S", ["S"], check_keys.check_secret("S")),
        ("P", ["P"], check_keys.check_phone("P")),
        ("Q", ["Q"], check_keys.check_phone("Q")),
    ]
    statuses = [status for _, status, _ in check_keys.run(env, checks)]
    assert statuses == ["FAIL", "OK", "FAIL"]
