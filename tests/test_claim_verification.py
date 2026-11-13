"""Deterministic unit tests; provider fixtures are synthetic and never external evidence."""

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
import uuid

from backend.app.claim_verification.claim_verifier import (
    ClaimVerificationService,
    SQLiteInvestigationClaimLoader,
)
from backend.app.claim_verification.evidence_analysis import assess_evidence
from backend.app.claim_verification.evidence_provider import DisabledEvidenceProvider
from backend.app.claim_verification.evidence_retrieval import EvidenceRetriever
from backend.app.claim_verification.exceptions import (
    InvestigationNotFound,
    InvalidClaim,
    InvalidEvidence,
    InvalidInvestigationId,
)
from backend.app.claim_verification.language_detection import UnavailableLanguageDetector
from backend.app.claim_verification.models import (
    ClaimInput,
    EvidenceRecord,
    LanguageDetection,
    SUPPORTED_LANGUAGES,
)
from backend.app.claim_verification.normalization import normalize_for_search
from backend.app.database import database_connection, initialize_database
from backend.app.repositories.investigations import insert_investigation


RETRIEVED_AT = "2026-11-13T12:00:00Z"


class _FixtureProvider:
    name = "synthetic-test-fixture"
    version = "test-1"

    def __init__(self, records=(), error=None):
        self.records = tuple(records)
        self.error = error
        self.queries = []

    def search(self, query):
        self.queries.append(query)
        if self.error:
            raise self.error
        return self.records


class _Detector:
    name = "test-language-detector"
    version = "test-1"

    def __init__(self, result):
        self.result = result

    def detect(self, _text):
        return self.result


def evidence(
    host: str,
    stance: str | None = None,
    *,
    publisher: str | None = None,
    outdated: bool = False,
    rating: str | None = None,
    source_attribution: str | None = None,
    url_path: str = "/review",
) -> EvidenceRecord:
    return EvidenceRecord(
        source_url=f"https://{host}{url_path}",
        provider_name="synthetic-fixture-provider",
        provider_version="fixture-1",
        retrieved_at=RETRIEVED_AT,
        publisher=publisher,
        publication_date=None,
        review_date=None,
        original_excerpt="Synthetic test excerpt; not a real fact check.",
        original_language="en",
        evidence_category="test_fixture",
        source_attribution=source_attribution or host,
        provider_rating=rating,
        assessment=stance,
        assessment_method="test_fixture_annotation" if stance else None,
        assessment_version="1" if stance else None,
        outdated=outdated,
    )


class ClaimVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.database_path = Path(self.temp_directory.name) / "claims.sqlite3"
        initialize_database(self.database_path)
        self.investigation_id = str(uuid.uuid4())

    def tearDown(self):
        self.temp_directory.cleanup()

    def _insert_claim(self, text="A test claim in its original form.", language="en", input_type="text"):
        created_at = "2026-11-13T12:00:00Z"
        with database_connection(self.database_path) as connection:
            insert_investigation(
                connection,
                investigation_id=self.investigation_id,
                created_at=created_at,
                status="received" if input_type == "text" else "awaiting_media",
                input_type=input_type,
                claim_text=None if input_type == "image" else text,
                language=language,
                source_reference=None,
            )
            connection.commit()

    def _service(self, provider=None, detector=None):
        retriever = EvidenceRetriever(
            providers=(provider,) if provider is not None else (DisabledEvidenceProvider(),)
        )
        return ClaimVerificationService(
            claim_loader=SQLiteInvestigationClaimLoader(self.database_path),
            evidence_retriever=retriever,
            language_detector=detector or UnavailableLanguageDetector(),
        )

    def test_claim_is_read_from_investigation_without_changing_original_text(self):
        original = "  హైదరాబాద్ లో   వర్షం పడింది  "
        self._insert_claim(original, language="te-IN")
        result = self._service().verify_investigation(self.investigation_id)

        self.assertEqual(result.original_claim, original)
        self.assertEqual(result.user_provided_language, "te-IN")
        self.assertEqual(result.detected_language, None)
        self.assertEqual(result.language_detection_status, "not_run_user_metadata_present")
        self.assertEqual(result.normalized_search_text, "హైదరాబాద్ లో వర్షం పడింది")
        self.assertIsNone(result.translation)
        self.assertIsNone(result.confidence)
        with database_connection(self.database_path) as connection:
            row = connection.execute(
                "SELECT claim_text, language FROM investigations WHERE id = ?",
                (self.investigation_id,),
            ).fetchone()
        self.assertEqual(row["claim_text"], original)
        self.assertEqual(row["language"], "te-IN")

    def test_all_seven_supported_metadata_tags_are_preserved_without_detection(self):
        for language in sorted(SUPPORTED_LANGUAGES):
            with self.subTest(language=language):
                self.investigation_id = str(uuid.uuid4())
                self._insert_claim(language=language)
                result = self._service().verify_investigation(self.investigation_id)
                self.assertEqual(result.user_provided_language, language)
                self.assertIsNone(result.detected_language)
                self.assertEqual(result.language_detection_status, "not_run_user_metadata_present")

    def test_english_hindi_and_telugu_claims_remain_original_and_untranslated(self):
        examples = (
            ("en", "The train arrives at 8."),
            ("hi", "ट्रेन 8 बजे आती है।"),
            ("te", "రైలు 8 గంటలకు వస్తుంది."),
        )
        for language, original in examples:
            with self.subTest(language=language):
                self.investigation_id = str(uuid.uuid4())
                self._insert_claim(original, language=language)
                result = self._service().verify_investigation(self.investigation_id)
                self.assertEqual(result.original_claim, original)
                self.assertEqual(result.user_provided_language, language)
                self.assertIsNone(result.translation)

    def test_missing_language_is_explicitly_unavailable_not_guessed(self):
        self._insert_claim("తెలుగు మరియు English mixed claim", language=None)
        result = self._service().verify_investigation(self.investigation_id)
        self.assertEqual(result.language_detection_status, "unavailable")
        self.assertIsNone(result.detected_language)
        self.assertEqual(result.outcome, "insufficient_evidence")
        self.assertIn("No evidence records", result.explanation)

    def test_detector_output_outside_supported_languages_becomes_uncertain(self):
        self._insert_claim(language=None)
        detector = _Detector(LanguageDetection(
            status="detected", detected_language="fr", detector_name="test", detector_version="1"
        ))
        result = self._service(detector=detector).verify_investigation(self.investigation_id)
        self.assertEqual(result.language_detection_status, "uncertain")
        self.assertIsNone(result.detected_language)

    def test_language_detection_exception_is_reported_without_guessing(self):
        class BrokenDetector:
            def detect(self, _text):
                raise RuntimeError("private detector detail")

        self._insert_claim(language=None)
        result = self._service(detector=BrokenDetector()).verify_investigation(self.investigation_id)
        self.assertEqual(result.language_detection_status, "failed")
        self.assertIsNone(result.detected_language)

    def test_normalization_is_separate_and_unicode_safe(self):
        text = "Cafe\u0301\t  CLAIM"
        self.assertEqual(normalize_for_search(text), "café claim")
        self.assertEqual(text, "Cafe\u0301\t  CLAIM")

    def test_supported_and_refuted_require_two_independent_source_domains(self):
        self._insert_claim()
        provider = _FixtureProvider((
            evidence("one.example", "supporting", publisher="Publisher One"),
            evidence("two.example", "supporting", publisher="Publisher Two"),
        ))
        supported = self._service(provider).verify_investigation(self.investigation_id)
        self.assertEqual(supported.outcome, "supported")
        self.assertEqual(len(supported.supporting_evidence_ids), 2)
        self.assertEqual(len(supported.evidence), 2)

        self.investigation_id = str(uuid.uuid4())
        self._insert_claim()
        provider = _FixtureProvider((
            evidence("one.example", "contradicting"),
            evidence("two.example", "contradicting"),
        ))
        refuted = self._service(provider).verify_investigation(self.investigation_id)
        self.assertEqual(refuted.outcome, "refuted")
        self.assertEqual(len(refuted.contradicting_evidence_ids), 2)

    def test_one_source_is_insufficient_and_conflicting_sources_are_mixed(self):
        single = evidence("one.example", "supporting")
        outcome, *_ = assess_evidence((single,))
        self.assertEqual(outcome, "insufficient_evidence")
        same_domain = (
            evidence("one.example", "supporting", publisher="Publisher One", url_path="/a"),
            evidence("one.example", "supporting", publisher="Publisher Two", url_path="/b"),
        )
        self.assertEqual(assess_evidence(same_domain)[0], "insufficient_evidence")

        support = evidence("one.example", "supporting", source_attribution="Source One")
        oppose = evidence("two.example", "contradicting", source_attribution="Source Two")
        outcome, _s, _c, _o, conflicts, _message = assess_evidence((support, oppose))
        self.assertEqual(outcome, "mixed_evidence")
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0].supporting_source, "Source One")
        self.assertEqual(conflicts[0].contradicting_source, "Source Two")

    def test_empty_unrated_outdated_and_rating_only_evidence_abstain(self):
        self.assertEqual(assess_evidence(())[0], "insufficient_evidence")
        rating_only = evidence("factcheck.example", rating="False")
        self.assertEqual(assess_evidence((rating_only,))[0], "unverified")
        old = evidence("old.example", "supporting", outdated=True)
        outcome, _s, _c, outdated, _conflicts, _message = assess_evidence((old,))
        self.assertEqual(outcome, "unverified")
        self.assertEqual(outdated, (old.evidence_id,))

    def test_evidence_validation_allows_missing_optional_provenance_without_invention(self):
        item = replace(
            evidence("source.example", publisher=None),
            source_attribution=None,
        )
        self.assertIsNone(item.publisher)
        self.assertIsNone(item.publication_date)
        self.assertIsNone(item.review_date)
        self.assertIsNone(item.source_attribution)
        self.assertEqual(item.original_excerpt, "Synthetic test excerpt; not a real fact check.")
        with self.assertRaises(InvalidEvidence):
            EvidenceRecord(
                source_url="javascript:alert(1)", provider_name="x", provider_version="1",
                retrieved_at=RETRIEVED_AT,
            )
        with self.assertRaises(InvalidEvidence):
            EvidenceRecord(
                source_url="https://source.example:invalid/path", provider_name="x",
                provider_version="1", retrieved_at=RETRIEVED_AT,
            )

    def test_duplicate_evidence_is_removed_and_source_allowlist_is_applied(self):
        first = evidence("allowed.example", url_path="/review#section")
        duplicate = evidence("allowed.example", url_path="/review")
        blocked = evidence("blocked.example")
        provider = _FixtureProvider((first, duplicate, blocked))
        result = EvidenceRetriever(
            providers=(provider,), allowed_source_domains=("allowed.example",)
        ).retrieve(None)
        self.assertEqual(result.records, (first,))
        self.assertEqual(result.duplicate_count, 1)

    def test_provider_empty_timeout_and_failure_are_reported_without_fabrication(self):
        empty = EvidenceRetriever(providers=(_FixtureProvider(),)).retrieve(None)
        self.assertEqual(empty.records, ())
        self.assertEqual(empty.provider_statuses[0].status, "empty")
        malformed = EvidenceRetriever(providers=(_FixtureProvider(("not evidence",)),)).retrieve(None)
        self.assertEqual(malformed.records, ())
        self.assertEqual(malformed.provider_statuses[0].status, "failed")

        timed_out = self._service(_FixtureProvider(error=TimeoutError())).verify_investigation(
            self._claim_for_verification()
        )
        self.assertEqual(timed_out.processing_status, "partial")
        self.assertEqual(timed_out.providers[0].error_code, "PROVIDER_TIMEOUT")
        self.assertEqual(timed_out.evidence, ())

        self.investigation_id = str(uuid.uuid4())
        self._insert_claim()
        failed = self._service(_FixtureProvider(error=RuntimeError("private details"))).verify_investigation(
            self.investigation_id
        )
        self.assertEqual(failed.providers[0].status, "failed")
        self.assertNotIn("private details", failed.providers[0].message)

    def _claim_for_verification(self):
        self._insert_claim()
        return self.investigation_id

    def test_default_provider_is_disabled_and_no_network_is_attempted(self):
        self._insert_claim()
        result = self._service().verify_investigation(self.investigation_id)
        self.assertEqual(result.providers[0].status, "disabled")
        self.assertEqual(result.providers[0].error_code, "PROVIDER_DISABLED")
        self.assertEqual(result.evidence, ())

    def test_missing_investigation_invalid_id_and_image_only_claim_fail_safely(self):
        loader = SQLiteInvestigationClaimLoader(self.database_path)
        with self.assertRaises(InvalidInvestigationId):
            loader.load_claim("not-a-uuid")
        with self.assertRaises(InvestigationNotFound):
            loader.load_claim(str(uuid.uuid4()))
        self._insert_claim(input_type="image")
        with self.assertRaises(InvalidClaim):
            loader.load_claim(self.investigation_id)

    def test_claim_input_rejects_blank_and_overlong_claims(self):
        with self.assertRaises(InvalidClaim):
            ClaimInput(str(uuid.uuid4()), " \t ")
        with self.assertRaises(InvalidClaim):
            ClaimInput(str(uuid.uuid4()), "x" * 10_001)


if __name__ == "__main__":
    unittest.main()
