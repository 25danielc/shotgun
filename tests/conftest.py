"""Shared fixtures. Tests marked `live` hit real APIs or ring a phone; they only run with
RUN_LIVE=1 (`make test-live`)."""

import os

import pytest


def pytest_collection_modifyitems(config, items):
    if os.environ.get("RUN_LIVE") == "1":
        return
    skip_live = pytest.mark.skip(reason="live test: set RUN_LIVE=1 to run")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)
