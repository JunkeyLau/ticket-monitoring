"""Local container liveness probe; no user data or credentials are sent."""
import urllib.request

with urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=8) as response:
    if response.status != 200:
        raise SystemExit(1)
