"""Offline tests for safe API-Sports error classification, no provider requests."""
import json
import unittest
from betpilot.quota_smart import QuotaClient, ProviderEndpointError


class FakeResponse:
    headers = {}
    def __init__(self, payload):
        self.payload = payload
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, *_args):
        return json.dumps(self.payload).encode("utf-8")


class ApiSportsClassificationTests(unittest.TestCase):
    def call(self, payload):
        client = QuotaClient("dummy-local-test-key", opener=lambda _req, timeout: FakeResponse(payload))
        return client._request("/fixtures", {"team": 1, "next": 1})

    def test_fixture_plan_restriction_has_explicit_safe_code(self):
        with self.assertRaises(ProviderEndpointError) as cm:
            self.call({"errors": {"plan": "sensitive subscription response"}})
        self.assertEqual(cm.exception.code, "API_SPORTS_PLAN_RESTRICTED")
        self.assertNotIn("sensitive", str(cm.exception))

    def test_quota_error_is_distinct_from_plan(self):
        with self.assertRaises(ProviderEndpointError) as cm:
            self.call({"errors": {"requests": "secret diagnostics"}})
        self.assertEqual(cm.exception.code, "API_SPORTS_QUOTA_REJECTED")

    def test_bad_query_is_not_misreported_as_plan(self):
        with self.assertRaises(ProviderEndpointError) as cm:
            self.call({"errors": {"parameters": "unexpected raw body"}})
        self.assertEqual(cm.exception.code, "API_SPORTS_PARAMETERS_REJECTED")

    def test_unrecognized_error_is_redacted(self):
        with self.assertRaises(ProviderEndpointError) as cm:
            self.call({"errors": ["private provider response"]})
        self.assertEqual(cm.exception.code, "API_SPORTS_PROVIDER_REJECTED")
        self.assertNotIn("private", str(cm.exception))

    def test_valid_empty_response_remains_valid(self):
        data, quota = self.call({"response": []})
        self.assertEqual(data["response"], [])
        self.assertIsNone(quota)

    def test_bad_response_shape_is_not_accepted(self):
        with self.assertRaises(ProviderEndpointError) as cm:
            self.call(["raw", "error"])
        self.assertEqual(cm.exception.code, "API_SPORTS_INVALID_RESPONSE")


if __name__ == "__main__":
    unittest.main()
