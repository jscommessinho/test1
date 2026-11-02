"""Only Bookmaker independent wrapper smoke; original RC15 suites run separately."""
import unittest
from pathlib import Path
from bookmaker.standalone import BookmakerError,validate_payload
from bookmaker.verdict_c import canonical_competition

class IndependentSmoke(unittest.TestCase):
    def test_competition_alias(self):
        self.assertEqual(canonical_competition("Italy Serie A 2026"),"Italy Serie A")
    def test_no_step5_imports(self):
        root=Path(__file__).resolve().parents[1]/"bookmaker"
        for name in ("standalone.py","__main__.py","api_football.py","quota_client.py","verdict_c.py"):
            source=(root/name).read_text("utf8")
            self.assertNotIn("from integrate_native import",source)
            self.assertNotIn("import integrate_native",source)
    def test_invalid_payload_abstains(self):
        with self.assertRaises(BookmakerError):
            validate_payload({"match":{},"odds":{}})
if __name__=="__main__":
    unittest.main()
