"""Smoke check: GET /health on the deployed URL. Usage: BASE_URL=https://... make smoke"""

import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()
base = (os.environ.get("BASE_URL") or os.environ.get("PUBLIC_BASE_URL") or "").rstrip("/")
if not base:
    sys.exit("Set BASE_URL or PUBLIC_BASE_URL")
response = httpx.get(f"{base}/health", timeout=10)
print(f"GET {base}/health -> {response.status_code} {response.text[:200]}")
sys.exit(0 if response.status_code == 200 else 1)
