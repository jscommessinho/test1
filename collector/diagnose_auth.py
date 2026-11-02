"""Safe, minimal PitchAPI connectivity probe. Does not print API key or response body."""
import json
import os
import urllib.error
import urllib.request

URL = "https://api.pitchapi.dev/v1/leagues"
key = os.environ.get("PITCHAPI_API_KEY", "").strip()
if not key:
    raise SystemExit("PITCHAPI_API_KEY missing")
if not key.startswith(("pk_live_", "pk_test_")):
    print("KEY_FORMAT: does not begin with the documented pk_live_/pk_test_ prefix")
else:
    print("KEY_FORMAT: recognized prefix; validity not established")


def record(label, status, headers, body):
    content_type = headers.get("Content-Type", "").split(";")[0]
    server = headers.get("Server", "unspecified")
    code = ""
    try:
        decoded = json.loads(body)
        if isinstance(decoded, dict):
            code = (decoded.get("error") or {}).get("code", "")
            if "data" in decoded:
                code = "JSON_DATA"
    except Exception:
        code = "NON_JSON"
    print(f"{label}: HTTP {status}, type={content_type}, server={server}, category={code}")
    return status == 200 and code == "JSON_DATA"

urllib_ok = False
req = urllib.request.Request(
    URL, headers={"X-API-KEY": key, "Accept": "application/json",
                  "User-Agent": "BETPILOT-Prematch/1.0"}, method="GET"
)
try:
    with urllib.request.urlopen(req, timeout=25) as response:
        urllib_ok = record("urllib", response.status, response.headers, response.read(12_000_000))
except urllib.error.HTTPError as e:
    record("urllib", e.code, e.headers, e.read(12_000_000))
except Exception as e:
    print("urllib: NETWORK_FAILURE "+type(e).__name__)

try:
    import httpx
    with httpx.Client(timeout=25, follow_redirects=False) as client:
        res = client.get(URL, headers={"X-API-KEY": key, "Accept": "application/json"})
    httpx_ok = record("httpx", res.status_code, res.headers, res.content)
except Exception as e:
    print("httpx: NETWORK_FAILURE "+type(e).__name__)
    httpx_ok = False

if not (urllib_ok or httpx_ok):
    raise SystemExit("PitchAPI connectivity/auth unresolved. No backfill attempted.")
print("PitchAPI access confirmed. Full historical download remains separate.")
