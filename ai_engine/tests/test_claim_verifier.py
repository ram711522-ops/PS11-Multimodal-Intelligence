"""Unit tests for the verification core using Python's standard library."""

import unittest

from ai_engine.verification import Claim, Evidence, EvidenceStance, Verdict, verify_claim


def item(source: str, stance: str, reliability: float = 1.0,
         relevance: float = 1.0, url: str | None = None) -> Evidence:
    return Evidence(source, url or f"https://{source.lower().replace(' ', '')}.example/article",
                    stance, reliability, relevance)


class EvidenceModelTests(unittest.TestCase):
    def test_scores_outside_range_or_non_finite_are_rejected(self) -> None:
        for score in (-0.01, 1.01, float("nan"), float("inf"), True):
            with self.subTest(score=score):
                with self.assertRaises(ValueError):
                    item("Source", "support", reliability=score)
                with self.assertRaises(ValueError):
                    item("Source", "support", relevance=score)

    def test_invalid_stance_is_rejected_and_valid_stance_is_typed(self) -> None:
        with self.assertRaises(ValueError):
            item("Source", "maybe")
        self.assertIs(item("Source", "support").stance, EvidenceStance.SUPPORT)

    def test_claim_requires_non_empty_text(self) -> None:
        with self.assertRaises(ValueError):
            Claim("  ")


class ClaimVerifierTests(unittest.TestCase):
    def test_verified(self) -> None:
        result = verify_claim("Claim", [item("A", "support"), item("B", "support"),
                                         item("C", "refute", relevance=0.2)])
        self.assertIs(result.verdict, Verdict.VERIFIED)

    def test_contradicted(self) -> None:
        result = verify_claim(Claim("Claim"), [item("A", "refute"), item("B", "refute")])
        self.assertIs(result.verdict, Verdict.CONTRADICTED)

    def test_balanced_conflict_is_misleading(self) -> None:
        result = verify_claim("Claim", [item("A", "support"), item("B", "refute")])
        self.assertIs(result.verdict, Verdict.MISLEADING)
        self.assertIn("conflicts", result.explanation)

    def test_empty_evidence_is_unverified(self) -> None:
        result = verify_claim("Claim", [])
        self.assertIs(result.verdict, Verdict.UNVERIFIED)
        self.assertEqual(result.confidence, 0.0)

    def test_low_quality_evidence_is_explicitly_unverified(self) -> None:
        result = verify_claim("Claim", [item("A", "support", 0.2, 0.2)])
        self.assertIs(result.verdict, Verdict.UNVERIFIED)
        self.assertIn("Insufficient", result.explanation)

    def test_weak_minority_does_not_create_misleading_verdict(self) -> None:
        result = verify_claim("Claim", [item("A", "support"), item("B", "support"),
                                         item("C", "support"), item("D", "refute", 1.0, 0.1)])
        self.assertIs(result.verdict, Verdict.VERIFIED)

    def test_duplicate_source_counts_only_its_strongest_item(self) -> None:
        result = verify_claim("Claim", [item("Same Publisher", "support"),
                                         item("Same Publisher", "support"),
                                         item("Other Publisher", "refute")])
        self.assertEqual(result.distinct_sources, 2)
        self.assertEqual(result.supporting_score, 1.0)
        self.assertIs(result.verdict, Verdict.MISLEADING)

    def test_confidence_is_documented_as_heuristic(self) -> None:
        result = verify_claim("Claim", [item("A", "support")])
        self.assertIn("not a calibrated probability", result.confidence_basis)

    def test_non_evidence_input_is_rejected(self) -> None:
        with self.assertRaises(TypeError):
            verify_claim("Claim", [object()])  # type: ignore[list-item]


if __name__ == "__main__":
    unittest.main()
