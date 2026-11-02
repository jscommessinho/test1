"""Offline regression tests for the independent Bookmaker API transport.

No real API requests, API keys, historical sources or model changes.
"""
import inspect
import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from urllib.error import HTTPError

from bookmaker_independent.bookmaker import api_football, quota_client


class FakeResponse:
    status = 200
    headers = {}

    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, _limit=-1):
        return json.dumps(self.payload).encode("utf-8")


class BookmakerTransportRecoveryTests(unittest.TestCase):
    def test_provider_request_uses_imported_urllib_and_does_not_call_network(self):
        client = api_football.FootballClient("local-test-key", max_requests=2)
        with patch.object(api_football, "urlopen",
                          return_value=FakeResponse({"response": []})) as opener:
            result = client.get("/fixtures", date="2026-11-02")
        self.assertEqual(result, {"response": []})
        self.assertEqual(client.count, 1)
        request = opener.call_args.args[0]
        self.assertIn("/fixtures?date=2026-11-02", request.full_url)
        self.assertEqual(request.get_header("X-apisports-key"), "local-test-key")

    def test_provider_http_error_is_classified_not_leaked(self):
        client = api_football.FootballClient("local-test-key")
        error = HTTPError("https://example.invalid", 401, "Unauthorized", None, None)
        with patch.object(api_football, "urlopen", side_effect=error):
            with self.assertRaisesRegex(api_football.AcquisitionError, "HTTP 401"):
                client.get("/fixtures", id=123)
        self.assertEqual(client.count, 1)

    def test_provider_refuses_unapproved_endpoint(self):
        client = api_football.FootballClient("local-test-key")
        with self.assertRaisesRegex(api_football.AcquisitionError, "Blocked endpoint"):
            client.get("/not-authorized")
        self.assertEqual(client.count, 0)

    def test_quota_client_has_no_step5_vendor_dependency(self):
        self.assertNotIn("BETPILOT_STEP5", inspect.getsource(quota_client))
        response = FakeResponse({
            "response": {"requests": {"limit_day": 100, "current": 5}}
        })
        def local_open(request, timeout=20):
            self.assertIn("/status", request.full_url)
            return response
        client = quota_client.QuotaClient("local-test-key", opener=local_open)
        status = client.preflight()
        self.assertEqual(status["quota_remaining_at_start"], 95)
        self.assertEqual(client.used, 0)


    def test_real_integration_uses_quota_client_used_counter(self):
        # Reproduces the 2026-11-02 Action failure, without making API calls.
        fixture = {"response": [{
            "fixture": {"id": 777, "date": "2027-03-01T20:00:00+00:00",
                        "status": {"short": "NS"}},
            "teams": {"home": {"id": 1, "name": "Home"},
                      "away": {"id": 2, "name": "Away"}},
            "league": {"id": 135, "country": "Italy",
                       "name": "Serie A", "season": 2026},
        }]}
        odds = {"response": [{
            "fixture": {"id": 777}, "update": "2026-10-30T12:00:00+00:00",
            "bookmakers": [{
                "id": 8, "name": "Bet365",
                "bets": [
                    {"id": 1, "values": [{"value": "Home", "odd": "2.10"},
                                        {"value": "Draw", "odd": "3.20"},
                                        {"value": "Away", "odd": "3.50"}]},
                    {"id": 5, "values": [{"value": "Over 2.5", "odd": "1.95"},
                                        {"value": "Under 2.5", "odd": "1.85"}]},
                    {"id": 8, "values": [{"value": "Yes", "odd": "1.80"},
                                        {"value": "No", "odd": "1.97"}]},
                ],
            }],
        }]}
        class StubQuotaClient:
            def __init__(self):
                self.used = 0
            def get(self, path, **query):
                self.used += 1
                return fixture if path == "/fixtures" else odds
        client = StubQuotaClient()
        payload, evidence = api_football.acquire(
            fixture_id=777, client=client,
            now=datetime(2026, 11, 2, tzinfo=timezone.utc))
        self.assertEqual(client.used, 2)
        self.assertEqual(evidence["api_requests"], 2)
        self.assertEqual(payload["odds"]["q1"], 2.1)
        self.assertEqual(payload["odds_snapshot"]["bookmaker"], "Bet365")


if __name__ == "__main__":
    unittest.main()
