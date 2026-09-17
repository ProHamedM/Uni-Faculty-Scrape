import unittest
from datetime import date

from tests.helpers import fixture_text
from unifaculty.classify import apply_rules, extract_json_object, name_on_page, normalize_for_match, quote_on_page, verify
from unifaculty.config import ResearchProfile
from unifaculty.extract import parse_html
from unifaculty.models import LLMDecision

PROFILE = ResearchProfile(current_degree="M.Sc.", target_fields=["model compression"], target_degree_levels=["phd"])


def decision(**over) -> LLMDecision:
    base = {
        "page_type": "person_profile",
        "people": [{"name": "Jane Example", "email": "jane.example@example-university.edu", "role": "supervisor"}],
        "position": {"exists": True, "degree_levels": ["PhD"], "evidence_quote": "I am looking for a PhD student in model compression to start in spring 2027."},
        "funding": {"status": "full", "evidence_quote": "The position is fully funded for four years (salary according to TV-L E13)."},
        "field_match": {"score": 0.9, "matched_topics": ["model compression"], "rationale": "direct fit"},
        "funding_confidence": 0.95,
    }
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = {**base[key], **value}
        else:
            base[key] = value
    return LLMDecision.model_validate(base)


class JsonParsingTests(unittest.TestCase):
    def test_fenced_json(self):
        self.assertEqual(extract_json_object('```json\n{"a": 1}\n```'), {"a": 1})

    def test_prose_around_json(self):
        self.assertEqual(extract_json_object('Sure! {"a": {"b": "}"}} hope this helps'), {"a": {"b": "}"}})

    def test_garbage(self):
        with self.assertRaises(ValueError):
            extract_json_object("no json here")

    def test_scores_are_clamped_and_levels_normalized(self):
        d = decision(field_match={"score": 7}, position={"degree_levels": ["Doctoral", "post-doc", "banana"]})
        self.assertEqual(d.field_match.score, 1.0)
        self.assertEqual(d.position.degree_levels, ["phd", "postdoc"])


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.page = parse_html(fixture_text("site/jane.html"), "https://www.example-university.edu/cs/people/jane-example")

    def test_quote_matching_tolerates_whitespace_and_quotes(self):
        self.assertTrue(quote_on_page("The position is  fully funded for four years", self.page.text))
        self.assertFalse(quote_on_page("The position comes with a free car", self.page.text))

    def test_names_with_titles(self):
        norm = normalize_for_match(self.page.text)
        self.assertTrue(name_on_page("Prof. Jane Example", norm))
        self.assertFalse(name_on_page("Prof. John Invented", norm))

    def test_hallucinated_person_and_email_are_removed(self):
        d = decision(people=[{"name": "Jane Example", "email": "jane@gmail.com"}, {"name": "Invented Person"}])
        v = verify(d, self.page)
        self.assertEqual([p.name for p in d.people], ["Jane Example"])
        self.assertIsNone(d.people[0].email)
        self.assertEqual(v.dropped_people, ["Invented Person"])
        self.assertEqual(v.dropped_emails, ["jane@gmail.com"])

    def test_good_decision_is_kept(self):
        d = decision()
        self.assertEqual(apply_rules(d, verify(d, self.page), PROFILE, date(2026, 9, 17)), [])


class RuleTests(unittest.TestCase):
    def setUp(self):
        self.page = parse_html(fixture_text("site/jane.html"), "https://www.example-university.edu/cs/people/jane-example")
        self.today = date(2026, 9, 17)

    def reasons(self, d, profile=PROFILE):
        return apply_rules(d, verify(d, self.page), profile, self.today)

    def test_invented_funding_quote_is_rejected(self):
        d = decision(funding={"evidence_quote": "Funding covers tuition and a generous stipend of 4000 EUR."})
        self.assertIn("funding_evidence_not_on_page", self.reasons(d))

    def test_expired_deadline(self):
        self.assertIn("expired", self.reasons(decision(position={"deadline": "2026-01-31"})))

    def test_off_field(self):
        self.assertIn("off_field", self.reasons(decision(field_match={"score": 0.3})))

    def test_no_funding(self):
        self.assertIn("no_funding", self.reasons(decision(funding={"status": "none"})))

    def test_unclear_funding_toggle(self):
        d = decision(funding={"status": "unclear", "evidence_quote": None})
        self.assertIn("funding_unclear", self.reasons(d))
        lenient = PROFILE.model_copy(update={"accept_unclear_funding": True})
        self.assertNotIn("funding_unclear", self.reasons(decision(funding={"status": "unclear", "evidence_quote": None}), lenient))

    def test_partial_toggle(self):
        strict = PROFILE.model_copy(update={"accept_partial_funding": False})
        self.assertIn("partial_funding_not_accepted", self.reasons(decision(funding={"status": "partial"}), strict))

    def test_wrong_degree_level(self):
        self.assertIn("degree_level_not_targeted", self.reasons(decision(position={"degree_levels": ["postdoc"]})))

    def test_no_position(self):
        self.assertIn("no_open_position", self.reasons(decision(position={"exists": False})))


if __name__ == "__main__":
    unittest.main()
