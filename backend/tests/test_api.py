"""End-to-end tests for the provisional investigation API."""

from datetime import datetime, timezone
import sqlite3

from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app


def make_client(tmp_path):
    app = create_app(Settings(database_path=tmp_path / "test.sqlite3"))
    return TestClient(app)


def test_health_reports_database_and_uninitialized_ai(tmp_path):
    response = make_client(tmp_path).get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok", "application": "ok", "database": "ok", "ai_modules": "not_initialized",
    }


def test_create_all_input_types_and_initial_status(tmp_path):
    client = make_client(tmp_path)
    text = client.post("/api/investigations", json={"input_type": "text", "claim_text": "  Claim  "})
    image = client.post("/api/investigations", json={"input_type": "image", "claim_text": None})
    combined = client.post("/api/investigations", json={"input_type": "text_image", "claim_text": "Claim"})
    assert [text.status_code, image.status_code, combined.status_code] == [201, 201, 201]
    assert text.json()["claim"]["text"] == "Claim"
    assert text.json()["status"] == "received"
    assert image.json()["status"] == combined.json()["status"] == "awaiting_media"
    assert image.json()["claim"] is None
    assert text.json()["claim"] == {"text": "Claim", "language": None, "normalized_text": None}
    assert combined.json()["claim"] == {"text": "Claim", "language": None, "normalized_text": None}
    assert all(response.json()["media"] == [] for response in (text, image, combined))


def test_creation_persists_optional_fields_and_utc_timestamps(tmp_path):
    client = make_client(tmp_path)
    response = client.post("/api/investigations", json={
        "input_type": "text", "claim_text": "Claim", "language": "en-US",
        "source_reference": "https://example.com/article",
    })
    data = response.json()
    assert data["claim"]["language"] == "en-US"
    assert data["source_reference"] == "https://example.com/article"
    assert data["claim"]["normalized_text"] is None
    assert data["latest_job"] is data["latest_result"] is data["failure"] is None
    assert datetime.fromisoformat(data["created_at"].replace("Z", "+00:00")).tzinfo == timezone.utc


def test_input_validation_errors_use_consistent_shape(tmp_path):
    client = make_client(tmp_path)
    cases = [
        {"input_type": "text", "claim_text": "   "},
        {"input_type": "text_image", "claim_text": None},
        {"input_type": "image", "claim_text": "not allowed"},
        {"input_type": "text", "claim_text": "x" * 10_001},
        {"input_type": "audio", "claim_text": "x"},
        {"input_type": "text", "claim_text": "x", "source_reference": "ftp://example.com"},
        {"input_type": "text", "claim_text": "x", "source_reference": "https://x/" + "a" * 2_050},
    ]
    for payload in cases:
        response = client.post("/api/investigations", json=payload)
        assert response.status_code == 422
        assert set(response.json()) == {"error"}
        assert set(response.json()["error"]) == {"code", "message", "details"}


def test_supported_missing_and_unsupported_languages(tmp_path):
    client = make_client(tmp_path)
    for language in ("en", "en-US", "en-GB", "hi", "hi-IN", "te", "te-IN", None):
        response = client.post("/api/investigations", json={
            "input_type": "text", "claim_text": "Claim", "language": language,
        })
        assert response.status_code == 201
        assert response.json()["claim"]["language"] == language
    assert client.post("/api/investigations", json={
        "input_type": "text", "claim_text": "Claim", "language": "fr-CA",
    }).json()["error"]["code"] == "LANGUAGE_NOT_SUPPORTED"


def test_supported_language_tags_are_case_insensitive_and_canonicalized(tmp_path):
    client = make_client(tmp_path)
    cases = (("EN", "en"), ("en-us", "en-US"), ("EN-gb", "en-GB"),
             ("HI", "hi"), ("hi-in", "hi-IN"), ("TE", "te"), ("te-in", "te-IN"))
    for language, canonical in cases:
        response = client.post("/api/investigations", json={
            "input_type": "text", "claim_text": "Claim", "language": language,
        })
        assert response.status_code == 201
        assert response.json()["claim"]["language"] == canonical


def test_valid_grandfathered_tags_are_unsupported_not_malformed(tmp_path):
    client = make_client(tmp_path)
    for language in ("i-klingon", "en-GB-oed", "art-lojban", "zh-min-nan", "no-bok"):
        response = client.post("/api/investigations", json={
            "input_type": "text", "claim_text": "Claim", "language": language,
        })
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "LANGUAGE_NOT_SUPPORTED"


def test_malformed_language_tags_have_specific_error_code(tmp_path):
    client = make_client(tmp_path)
    for language in ("en_US", "en--US", "123", "en-u", "en-u-ca-gregory-u-nu-latn"):
        response = client.post("/api/investigations", json={
            "input_type": "text", "claim_text": "Claim", "language": language,
        })
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "INVALID_LANGUAGE_TAG"


def test_get_investigation_and_unknown_id(tmp_path):
    client = make_client(tmp_path)
    created = client.post("/api/investigations", json={"input_type": "image"}).json()
    fetched = client.get(f"/api/investigations/{created['investigation_id']}")
    assert fetched.status_code == 200
    assert fetched.json() == created
    missing = client.get("/api/investigations/00000000-0000-0000-0000-000000000000")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "INVESTIGATION_NOT_FOUND"


def test_retrieval_returns_null_claim_for_image_and_object_for_text(tmp_path):
    client = make_client(tmp_path)
    image = client.post("/api/investigations", json={"input_type": "image"}).json()
    text = client.post("/api/investigations", json={"input_type": "text", "claim_text": "Claim"}).json()
    image_response = client.get(f"/api/investigations/{image['investigation_id']}")
    text_response = client.get(f"/api/investigations/{text['investigation_id']}")
    assert image_response.json()["claim"] is None
    assert text_response.json()["claim"] == {
        "text": "Claim", "language": None, "normalized_text": None,
    }


def test_invalid_uuid_returns_consistent_not_found_error(tmp_path):
    response = make_client(tmp_path).get("/api/investigations/nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "INVESTIGATION_NOT_FOUND"


def assert_error_envelope(response, status_code, code):
    assert response.status_code == status_code
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "details"}
    assert body["error"]["code"] == code
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]
    assert isinstance(body["error"]["details"], dict)


def test_unknown_route_and_unsupported_method_use_standard_errors(tmp_path):
    client = make_client(tmp_path)
    assert_error_envelope(client.get("/does-not-exist"), 404, "NOT_FOUND")
    assert_error_envelope(client.put("/health"), 405, "METHOD_NOT_ALLOWED")


def test_unknown_investigation_uses_standard_error_envelope(tmp_path):
    response = make_client(tmp_path).get("/api/investigations/00000000-0000-0000-0000-000000000000")
    assert_error_envelope(response, 404, "INVESTIGATION_NOT_FOUND")


def test_health_failure_uses_standard_error_envelope(tmp_path):
    app = create_app(Settings(database_path=tmp_path / "health.sqlite3"))

    class BrokenDatabase:
        def connect(self):
            raise OSError("private filesystem detail")

    app.state.database = BrokenDatabase()
    response = TestClient(app).get("/health")
    assert_error_envelope(response, 503, "HEALTH_CHECK_FAILED")
    assert "private filesystem detail" not in response.text


def test_unexpected_errors_are_safe_and_use_standard_shape(tmp_path):
    app = create_app(Settings(database_path=tmp_path / "error.sqlite3"))

    def fail_create(payload):
        raise RuntimeError("internal detail must not leak")

    app.state.investigation_service.repository.create = fail_create
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post("/api/investigations", json={"input_type": "text", "claim_text": "Claim"})
    assert response.status_code == 500
    assert response.json() == {"error": {
        "code": "INTERNAL_SERVER_ERROR",
        "message": "An unexpected server error occurred.",
        "details": {},
    }}


def test_history_pagination_and_deterministic_ordering(tmp_path):
    client = make_client(tmp_path)
    ids = []
    for claim in ("one", "two", "three"):
        ids.append(client.post("/api/investigations", json={"input_type": "text", "claim_text": claim}).json()["investigation_id"])
    with sqlite3.connect(tmp_path / "test.sqlite3") as connection:
        tied_time = "2026-01-01T00:00:00.000000Z"
        connection.execute("UPDATE investigations SET created_at = ?, updated_at = ?", (tied_time, tied_time))
    page = client.get("/api/investigations?limit=2&offset=0").json()
    assert page["total"] == 3 and page["limit"] == 2 and page["offset"] == 0
    assert [item["investigation_id"] for item in page["items"]] == sorted(ids, reverse=True)[:2]
    assert client.get("/api/investigations?limit=2&offset=2").json()["items"][0]["investigation_id"] == sorted(ids, reverse=True)[2]
    assert client.get("/api/investigations?limit=0").status_code == 422
    assert client.get("/api/investigations?limit=101").status_code == 422
    assert client.get("/api/investigations?offset=-1").status_code == 422


def test_default_history_response(tmp_path):
    response = make_client(tmp_path).get("/api/investigations")
    assert response.json() == {"items": [], "total": 0, "limit": 20, "offset": 0}


def test_database_persists_after_app_restart(tmp_path):
    path = tmp_path / "persistent.sqlite3"
    first_client = TestClient(create_app(Settings(database_path=path)))
    created = first_client.post("/api/investigations", json={
        "input_type": "text", "claim_text": "Persist me", "language": "te-IN",
    }).json()
    second_client = TestClient(create_app(Settings(database_path=path)))
    response = second_client.get(f"/api/investigations/{created['investigation_id']}")
    assert response.status_code == 200
    assert response.json()["claim"]["text"] == "Persist me"
    assert response.json()["claim"]["language"] == "te-IN"
