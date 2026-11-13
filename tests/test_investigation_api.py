"""Integration tests for investigation API Contract v1.2 behavior."""

from pathlib import Path
import asyncio
import sqlite3
import tempfile
import unittest
import uuid

import httpx

from backend.app.config import Settings
from backend.app.database import database_connection
from backend.app.main import create_app


class InvestigationApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory(
            dir=Path(__file__).resolve().parents[1]
        )
        self.database_path = Path(self.temp_directory.name) / "investigation-api.sqlite3"
        self.settings = Settings(database_path=self.database_path)

    def tearDown(self) -> None:
        self.temp_directory.cleanup()

    def request(self, app, method: str, path: str, *, json_body=None) -> httpx.Response:
        async def send_request() -> httpx.Response:
            async with app.router.lifespan_context(app):
                transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
                async with httpx.AsyncClient(
                    transport=transport,
                    base_url="http://testserver",
                ) as client:
                    return await client.request(method, path, json=json_body)

        return asyncio.run(send_request())

    def create(self, payload: dict) -> httpx.Response:
        return self.request(
            create_app(self.settings),
            "POST",
            "/api/investigations",
            json_body=payload,
        )

    def test_create_all_three_input_types_and_initial_statuses(self) -> None:
        cases = (
            ({"input_type": "text", "claim_text": "Text claim"}, "received"),
            ({"input_type": "image", "claim_text": None}, "awaiting_media"),
            (
                {"input_type": "text_image", "claim_text": "Text and image claim"},
                "awaiting_media",
            ),
        )
        for payload, expected_status in cases:
            with self.subTest(input_type=payload["input_type"]):
                response = self.create(payload)
                self.assertEqual(response.status_code, 201)
                self.assertEqual(response.json()["input_type"], payload["input_type"])
                self.assertEqual(response.json()["status"], expected_status)

    def test_creation_keeps_claim_text_string_or_null(self) -> None:
        text_claim = "Submitted claim stays a string in the request and database."
        text_creation = self.create(
            {"input_type": "text", "claim_text": text_claim}
        )
        image_creation = self.create({"input_type": "image", "claim_text": None})

        self.assertEqual(text_creation.status_code, 201)
        self.assertEqual(image_creation.status_code, 201)
        with database_connection(self.database_path) as connection:
            stored_text = connection.execute(
                "SELECT claim_text FROM investigations WHERE id = ?",
                (text_creation.json()["investigation_id"],),
            ).fetchone()["claim_text"]
            stored_image_claim = connection.execute(
                "SELECT claim_text FROM investigations WHERE id = ?",
                (image_creation.json()["investigation_id"],),
            ).fetchone()["claim_text"]
        self.assertEqual(stored_text, text_claim)
        self.assertIsNone(stored_image_claim)

    def test_creation_response_has_exact_contract_fields(self) -> None:
        response = self.create({"input_type": "text", "claim_text": "Example claim"})

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(
            set(body),
            {"investigation_id", "input_type", "status", "created_at", "message"},
        )
        self.assertEqual(uuid.UUID(body["investigation_id"]).version, 4)
        self.assertTrue(body["created_at"].endswith("Z"))
        self.assertTrue(body["message"])

    def test_retrieval_response_has_exact_contract_fields(self) -> None:
        created = self.create(
            {
                "input_type": "text_image",
                "claim_text": "Claim with future media",
                "language": "hi-IN",
                "source_reference": "https://example.org/source",
            }
        ).json()

        response = self.request(
            create_app(self.settings),
            "GET",
            f"/api/investigations/{created['investigation_id']}",
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(
            set(body),
            {
                "investigation_id",
                "input_type",
                "status",
                "claim",
                "source_reference",
                "created_at",
                "updated_at",
                "media",
                "latest_job",
                "latest_result",
                "failure",
            },
        )
        self.assertEqual(body["investigation_id"], created["investigation_id"])
        self.assertEqual(
            body["claim"],
            {
                "text": "Claim with future media",
                "language": "hi-IN",
                "normalized_text": None,
            },
        )
        self.assertEqual(body["media"], [])
        self.assertIsNone(body["latest_job"])
        self.assertIsNone(body["latest_result"])
        self.assertIsNone(body["failure"])

    def test_image_only_retrieval_uses_null_claim_in_single_and_history(self) -> None:
        created = self.create({"input_type": "image", "claim_text": None}).json()
        app = create_app(self.settings)
        single = self.request(
            app,
            "GET",
            f"/api/investigations/{created['investigation_id']}",
        )
        history = self.request(app, "GET", "/api/investigations")
        history_item = next(
            item
            for item in history.json()["items"]
            if item["investigation_id"] == created["investigation_id"]
        )

        self.assertEqual(single.status_code, 200)
        self.assertIsNone(single.json()["claim"])
        self.assertIsNone(history_item["claim"])
        self.assertEqual(single.json()["claim"], history_item["claim"])

    def test_single_and_history_text_claims_have_identical_representation(self) -> None:
        submitted = "Claim appears as text without normalization."
        created = self.create(
            {"input_type": "text", "claim_text": submitted, "language": "en-GB"}
        ).json()
        app = create_app(self.settings)
        single = self.request(
            app,
            "GET",
            f"/api/investigations/{created['investigation_id']}",
        )
        history = self.request(app, "GET", "/api/investigations")
        history_item = next(
            item
            for item in history.json()["items"]
            if item["investigation_id"] == created["investigation_id"]
        )
        expected_claim = {
            "text": submitted,
            "language": "en-GB",
            "normalized_text": None,
        }

        self.assertEqual(single.status_code, 200)
        self.assertEqual(single.json()["claim"], expected_claim)
        self.assertEqual(history_item["claim"], expected_claim)
        self.assertEqual(single.json()["claim"], history_item["claim"])

    def test_image_rejects_non_null_claim(self) -> None:
        response = self.create({"input_type": "image", "claim_text": "Not allowed"})
        self.assertEqual(response.status_code, 422)

    def test_invalid_input_type_returns_standard_error(self) -> None:
        response = self.create({"input_type": "audio", "claim_text": "Claim"})
        self.assert_standard_error(response, 422, "validation_error")

    def test_missing_claim_rejected_for_text_and_text_image(self) -> None:
        for input_type in ("text", "text_image"):
            with self.subTest(input_type=input_type):
                response = self.create({"input_type": input_type})
                self.assert_standard_error(response, 422, "validation_error")

    def test_whitespace_only_claim_rejected(self) -> None:
        for input_type in ("text", "text_image"):
            with self.subTest(input_type=input_type):
                response = self.create(
                    {"input_type": input_type, "claim_text": " \t\n "}
                )
                self.assert_standard_error(response, 422, "validation_error")

    def test_claim_length_limit(self) -> None:
        accepted = self.create({"input_type": "text", "claim_text": "x" * 10_000})
        rejected = self.create({"input_type": "text", "claim_text": "x" * 10_001})

        self.assertEqual(accepted.status_code, 201)
        self.assert_standard_error(rejected, 422, "validation_error")

    def test_source_reference_requires_http_or_https_url(self) -> None:
        for source_reference in (
            "ftp://example.org/file",
            "https://",
            "not a URL",
            "https://example.org/has space",
        ):
            with self.subTest(source_reference=source_reference):
                response = self.create(
                    {
                        "input_type": "text",
                        "claim_text": "Claim",
                        "source_reference": source_reference,
                    }
                )
                self.assert_standard_error(response, 422, "validation_error")

    def test_source_reference_length_limit(self) -> None:
        prefix = "https://example.org/"
        valid_url = prefix + "a" * (2_048 - len(prefix))
        accepted = self.create(
            {
                "input_type": "text",
                "claim_text": "Claim",
                "source_reference": valid_url,
            }
        )
        rejected = self.create(
            {
                "input_type": "text",
                "claim_text": "Claim",
                "source_reference": valid_url + "a",
            }
        )

        self.assertEqual(accepted.status_code, 201)
        self.assert_standard_error(rejected, 422, "validation_error")

    def test_language_bcp47_validation_and_automatic_mode(self) -> None:
        for language in (
            "en",
            "en-US",
            "en-GB",
            "hi",
            "hi-IN",
            "te",
            "te-IN",
        ):
            with self.subTest(language=language):
                response = self.create(
                    {"input_type": "text", "claim_text": "Claim", "language": language}
                )
                self.assertEqual(response.status_code, 201)
                stored_language = self.database_language_for(
                    response.json()["investigation_id"]
                )
                self.assertEqual(stored_language, language)

        canonicalized = self.create(
            {"input_type": "text", "claim_text": "Claim", "language": "hI-in"}
        )
        self.assertEqual(canonicalized.status_code, 201)
        language_row = self.database_language_for(canonicalized.json()["investigation_id"])
        self.assertEqual(language_row, "hi-IN")

        for payload in (
            {"input_type": "text", "claim_text": "Claim"},
            {"input_type": "text", "claim_text": "Claim", "language": None},
        ):
            with self.subTest(language=payload.get("language", "missing")):
                automatic = self.create(payload)
                self.assertEqual(automatic.status_code, 201)
                self.assertIsNone(
                    self.database_language_for(automatic.json()["investigation_id"])
                )

    def test_valid_unsupported_language_returns_contract_error(self) -> None:
        response = self.create(
            {"input_type": "text", "claim_text": "Claim", "language": "fr-FR"}
        )
        self.assert_standard_error(response, 422, "LANGUAGE_NOT_SUPPORTED")

    def test_invalid_language_tags_rejected(self) -> None:
        for language in ("", "en_US", "en--US", "e", "en-a", "en-x", "en-1901-1901"):
            with self.subTest(language=language):
                response = self.create(
                    {"input_type": "text", "claim_text": "Claim", "language": language}
                )
                self.assert_standard_error(response, 422, "INVALID_LANGUAGE_TAG")

    def test_invalid_uuid_returns_standard_422(self) -> None:
        response = self.request(
            create_app(self.settings),
            "GET",
            "/api/investigations/not-a-uuid",
        )
        self.assert_standard_error(response, 422, "invalid_uuid")

    def test_unknown_investigation_returns_standard_404(self) -> None:
        response = self.request(
            create_app(self.settings),
            "GET",
            f"/api/investigations/{uuid.uuid4()}",
        )
        self.assert_standard_error(response, 404, "not_found")

    def test_history_returns_total_limit_offset_and_default_values(self) -> None:
        self.create({"input_type": "text", "claim_text": "First"})
        self.create({"input_type": "image", "claim_text": None})

        response = self.request(
            create_app(self.settings),
            "GET",
            "/api/investigations",
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(set(body), {"items", "total", "limit", "offset"})
        self.assertEqual(body["total"], 2)
        self.assertEqual(body["limit"], 20)
        self.assertEqual(body["offset"], 0)
        self.assertEqual(len(body["items"]), 2)
        self.assertIn("investigation_id", body["items"][0])
        self.assertNotIn("id", body["items"][0])

    def test_offset_pagination_and_deterministic_order(self) -> None:
        created = [
            self.create({"input_type": "text", "claim_text": f"Claim {i}"}).json()
            for i in range(5)
        ]
        shared_timestamp = "2026-11-13T08:15:30.000000Z"
        with database_connection(self.database_path) as connection:
            connection.execute(
                "UPDATE investigations SET created_at = ?, updated_at = ?",
                (shared_timestamp, shared_timestamp),
            )
            connection.commit()

        app = create_app(self.settings)
        first_page = self.request(app, "GET", "/api/investigations?limit=2&offset=0")
        second_page = self.request(app, "GET", "/api/investigations?limit=2&offset=2")
        last_page = self.request(app, "GET", "/api/investigations?limit=2&offset=4")

        self.assertEqual(
            [
                (response.status_code, response.json()["total"], response.json()["limit"], response.json()["offset"])
                for response in (first_page, second_page, last_page)
            ],
            [(200, 5, 2, 0), (200, 5, 2, 2), (200, 5, 2, 4)],
        )
        ids = [
            item["investigation_id"]
            for response in (first_page, second_page, last_page)
            for item in response.json()["items"]
        ]
        self.assertEqual(
            ids,
            sorted((item["investigation_id"] for item in created), reverse=True),
        )

    def test_invalid_pagination_parameters_return_standard_422(self) -> None:
        app = create_app(self.settings)
        for path in (
            "/api/investigations?limit=0",
            "/api/investigations?limit=101",
            "/api/investigations?offset=-1",
            "/api/investigations?offset=not-an-integer",
        ):
            with self.subTest(path=path):
                self.assert_standard_error(
                    self.request(app, "GET", path), 422, "validation_error"
                )

    def test_records_persist_after_application_restart(self) -> None:
        created = self.create(
            {"input_type": "text", "claim_text": "Persistent claim"}
        ).json()

        response = self.request(
            create_app(self.settings),
            "GET",
            f"/api/investigations/{created['investigation_id']}",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["claim"],
            {"text": "Persistent claim", "language": None, "normalized_text": None},
        )

    def test_unexpected_database_error_is_sanitized(self) -> None:
        app = create_app(self.settings)

        async def request_with_invalid_database_path() -> httpx.Response:
            async with app.router.lifespan_context(app):
                app.state.settings = Settings(database_path=Path(self.temp_directory.name))
                transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
                async with httpx.AsyncClient(
                    transport=transport,
                    base_url="http://testserver",
                ) as client:
                    return await client.get("/api/investigations")

        with self.assertLogs("backend.app.errors", level="ERROR"):
            response = asyncio.run(request_with_invalid_database_path())
        self.assert_standard_error(response, 500, "database_error")
        self.assertNotIn(self.temp_directory.name, response.text)

    def database_language_for(self, investigation_id: str) -> str | None:
        with database_connection(self.database_path) as connection:
            row = connection.execute(
                "SELECT language FROM investigations WHERE id = ?",
                (investigation_id,),
            ).fetchone()
        return row["language"]

    def assert_standard_error(
        self,
        response: httpx.Response,
        status_code: int,
        code: str,
    ) -> None:
        self.assertEqual(response.status_code, status_code)
        body = response.json()
        self.assertEqual(set(body), {"error"})
        self.assertEqual(set(body["error"]), {"code", "message", "details"})
        self.assertEqual(body["error"]["code"], code)
        self.assertIsInstance(body["error"]["message"], str)
        self.assertIsInstance(body["error"]["details"], dict)
        self.assertNotIn(self.temp_directory.name, response.text)


if __name__ == "__main__":
    unittest.main()
