"""Persistent v1 processing API and AI workflow integration tests."""

from hashlib import sha256
from io import BytesIO
import sqlite3
from uuid import uuid4

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from ai_engine.media_processing import MediaProcessingPipeline
from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.media_storage import (
    CallbackMediaSourceStorage,
    MediaSourceUnavailable,
    StoredPhase3Media,
    UnavailableMediaSourceStorage,
)


def make_image() -> bytes:
    image = Image.new("RGB", (64, 48), (120, 30, 80))
    stream = BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


class FixtureMediaStorage:
    available = True

    def __init__(self, content: bytes | None = None):
        self.content = content if content is not None else make_image()
        self.media = {}

    def add(self, investigation_id: str, media_id: str):
        self.media[(investigation_id, media_id)] = StoredPhase3Media(
            upload_response={
                "investigation_id": investigation_id,
                "media_id": media_id,
                "filename": "upload.png",
                "mime_type": "image/png",
                "size_bytes": len(self.content),
                "checksum": sha256(self.content).hexdigest(),
                "width": 64,
                "height": 48,
                "uploaded_at": "2026-11-13T10:00:00Z",
            },
            original_image_bytes=self.content,
        )

    def load_media(self, investigation_id: str, media_id: str) -> StoredPhase3Media:
        try:
            return self.media[(investigation_id, media_id)]
        except KeyError as exc:
            raise MediaSourceUnavailable("not found") from exc


def make_client(tmp_path, storage=None, pipeline=None):
    app = create_app(
        Settings(database_path=tmp_path / "processing.sqlite3"),
        media_storage=storage,
        processing_pipeline=pipeline,
    )
    return TestClient(app), app


def create_investigation(client):
    response = client.post("/api/investigations", json={"input_type": "image"})
    assert response.status_code == 201
    return response.json()["investigation_id"]


def assert_error(response, status, code):
    assert response.status_code == status
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "details"}
    assert body["error"]["code"] == code
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]
    assert isinstance(body["error"]["details"], dict)


def test_processing_schema_migration_is_additive_and_preserves_phase2_data(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(
        """CREATE TABLE investigations (
            investigation_id TEXT PRIMARY KEY,
            input_type TEXT NOT NULL CHECK(input_type IN ('text', 'image', 'text_image')),
            status TEXT NOT NULL CHECK(status IN ('received', 'awaiting_media')),
            claim_text TEXT, language TEXT, source_reference TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )"""
    )
    investigation_id = str(uuid4())
    connection.execute(
        "INSERT INTO investigations VALUES (?, 'image', 'awaiting_media', NULL, NULL, NULL, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
        (investigation_id,),
    )
    before_ddl = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='investigations'"
    ).fetchone()[0]
    connection.commit()
    connection.close()

    client = TestClient(create_app(Settings(database_path=path), media_storage=FixtureMediaStorage()))
    with sqlite3.connect(path) as migrated:
        after_ddl = migrated.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='investigations'"
        ).fetchone()[0]
        assert after_ddl == before_ddl
        assert migrated.execute("SELECT COUNT(*) FROM investigations").fetchone()[0] == 1
        tables = {row[0] for row in migrated.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"processing_jobs", "processing_results", "investigations"} <= tables
        assert "media_assets" not in tables
        indexes = {row[0] for row in migrated.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )}
        assert {
            "idx_processing_jobs_investigation_created",
            "idx_processing_results_job_created",
        } <= indexes
        assert migrated.execute("PRAGMA foreign_key_list(processing_jobs)").fetchall() == []
        result_fks = migrated.execute("PRAGMA foreign_key_list(processing_results)").fetchall()
        assert len(result_fks) == 1 and result_fks[0][2] == "processing_jobs"
    migrated.close()
    assert client.get(f"/api/investigations/{investigation_id}").status_code == 200


def test_create_job_runs_existing_ai_workflow_and_persists_results_after_restart(tmp_path):
    storage = FixtureMediaStorage()
    client, _ = make_client(tmp_path, storage=storage)
    investigation_id = create_investigation(client)
    media_id = str(uuid4())
    storage.add(investigation_id, media_id)

    created = client.post("/api/v1/processing-jobs", json={
        "investigation_id": investigation_id,
        "media_id": media_id,
    })
    assert created.status_code == 202
    assert created.json()["api_version"] == "1.0"
    job = created.json()["job"]
    assert job["processing_status"] == "queued"
    assert job["media_id"] == media_id
    assert "processing.sqlite3" not in created.text

    status = client.get(f"/api/v1/processing-jobs/{job['job_id']}")
    assert status.status_code == 200
    assert status.json()["job"]["processing_status"] == "completed"
    assert status.json()["job"]["started_at"].endswith("Z")
    assert status.json()["job"]["completed_at"].endswith("Z")
    assert status.json()["job"]["failure"] is None

    results = client.get(f"/api/v1/processing-jobs/{job['job_id']}/results")
    body = results.json()
    assert results.status_code == 200
    assert body["total"] == 3
    assert {item["algorithm"] for item in body["items"]} == {
        "image_preprocessing", "phash", "ps11_media_processing",
    }
    unified = next(item for item in body["items"] if item["result_type"] == "media_intelligence_pipeline")
    assert unified["algorithm_version"] == "1.0.0"
    assert unified["result_data"]["source_sha256"] == sha256(storage.content).hexdigest()
    assert unified["result_data"]["derived_media"]["source_media_id"] == media_id
    assert unified["result_data"]["perceptual_hash"]["algorithm_version"] == "dct-64-v1"

    restarted, _ = make_client(tmp_path, storage=storage)
    assert restarted.get(f"/api/v1/processing-jobs/{job['job_id']}").json() == status.json()
    assert restarted.get(f"/api/v1/processing-jobs/{job['job_id']}/results").json() == body


def test_unavailable_original_media_storage_fails_fast_with_standard_error(tmp_path):
    client, _ = make_client(tmp_path)
    investigation_id = create_investigation(client)
    response = client.post("/api/v1/processing-jobs", json={
        "investigation_id": investigation_id,
        "media_id": str(uuid4()),
    })
    assert_error(response, 503, "PROCESSING_API_UNAVAILABLE")
    with sqlite3.connect(tmp_path / "processing.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM processing_jobs").fetchone()[0] == 0


def test_invalid_missing_and_unknown_job_requests_use_standard_errors(tmp_path):
    client, _ = make_client(tmp_path, storage=FixtureMediaStorage())
    assert_error(client.get("/api/v1/processing-jobs/not-a-uuid"), 400, "INVALID_PROCESSING_REQUEST")
    unknown = str(uuid4())
    assert_error(client.get(f"/api/v1/processing-jobs/{unknown}"), 404, "PROCESSING_JOB_NOT_FOUND")
    assert_error(client.get(f"/api/v1/processing-jobs/{unknown}/results"), 404, "PROCESSING_JOB_NOT_FOUND")
    assert_error(client.post(f"/api/v1/processing-jobs/{unknown}/retry", json={"retry_failed": True}), 404, "PROCESSING_JOB_NOT_FOUND")
    assert_error(client.post("/api/v1/processing-jobs", json={"investigation_id": "bad", "media_id": str(uuid4())}), 422, "VALIDATION_ERROR")


def test_missing_investigation_and_nonfailed_retry_are_rejected(tmp_path):
    storage = FixtureMediaStorage()
    client, _ = make_client(tmp_path, storage=storage)
    assert_error(client.post("/api/v1/processing-jobs", json={
        "investigation_id": str(uuid4()), "media_id": str(uuid4()),
    }), 404, "INVESTIGATION_NOT_FOUND")

    investigation_id = create_investigation(client)
    media_id = str(uuid4())
    storage.add(investigation_id, media_id)
    job = client.post("/api/v1/processing-jobs", json={
        "investigation_id": investigation_id, "media_id": media_id,
    }).json()["job"]
    assert_error(
        client.post(f"/api/v1/processing-jobs/{job['job_id']}/retry", json={"retry_failed": True}),
        409,
        "PROCESSING_RETRY_NOT_ALLOWED",
    )
    assert_error(
        client.post(f"/api/v1/processing-jobs/{job['job_id']}/retry", json={"retry_failed": False}),
        422,
        "VALIDATION_ERROR",
    )


def test_unstarted_job_can_be_marked_skipped_and_retrieved(tmp_path):
    storage = FixtureMediaStorage()
    client, app = make_client(tmp_path, storage=storage)
    investigation_id = create_investigation(client)
    media_id = str(uuid4())
    storage.add(investigation_id, media_id)
    job = app.state.processing_service.create_job(investigation_id, media_id)
    skipped = app.state.processing_service.skip_job(job.job_id)
    assert skipped.processing_status.value == "skipped"
    response = client.get(f"/api/v1/processing-jobs/{job.job_id}")
    assert response.status_code == 200
    assert response.json()["job"]["processing_status"] == "skipped"


def test_corrupt_image_becomes_persisted_safe_processing_failure(tmp_path):
    storage = FixtureMediaStorage(b"invalid image bytes")
    client, _ = make_client(tmp_path, storage=storage)
    investigation_id = create_investigation(client)
    media_id = str(uuid4())
    storage.add(investigation_id, media_id)
    created = client.post("/api/v1/processing-jobs", json={
        "investigation_id": investigation_id, "media_id": media_id,
    })
    job_id = created.json()["job"]["job_id"]
    status = client.get(f"/api/v1/processing-jobs/{job_id}").json()["job"]
    assert status["processing_status"] == "failed"
    assert status["failure"]["code"] == "INVALID_IMAGE"
    assert "invalid image bytes" not in str(status)
    assert client.get(f"/api/v1/processing-jobs/{job_id}/results").json()["items"] == []


def test_retry_failure_preserves_previous_successful_stage_results(tmp_path):
    storage = FixtureMediaStorage()

    def fail_hash(_normalized):
        raise RuntimeError("private decoder detail")

    pipeline = MediaProcessingPipeline(hasher=fail_hash)
    client, _ = make_client(tmp_path, storage=storage, pipeline=pipeline)
    investigation_id = create_investigation(client)
    media_id = str(uuid4())
    storage.add(investigation_id, media_id)
    job = client.post("/api/v1/processing-jobs", json={
        "investigation_id": investigation_id, "media_id": media_id,
    }).json()["job"]
    job_id = job["job_id"]
    first_status = client.get(f"/api/v1/processing-jobs/{job_id}").json()["job"]
    assert first_status["processing_status"] == "failed"
    assert first_status["failure"]["code"] == "PHASH_FAILED"
    assert "private decoder detail" not in str(first_status)
    first_results = client.get(f"/api/v1/processing-jobs/{job_id}/results").json()["items"]
    assert len(first_results) == 1
    assert first_results[0]["result_type"] == "normalized_image"
    first_result_ids = {item["result_id"] for item in first_results}

    retry = client.post(f"/api/v1/processing-jobs/{job_id}/retry", json={"retry_failed": True})
    assert retry.status_code == 202
    assert retry.json()["job"]["processing_status"] == "queued"
    final_status = client.get(f"/api/v1/processing-jobs/{job_id}").json()["job"]
    assert final_status["processing_status"] == "failed"
    assert final_status["failure"]["code"] == "PHASH_FAILED"
    final_results = client.get(f"/api/v1/processing-jobs/{job_id}/results").json()["items"]
    assert first_result_ids <= {item["result_id"] for item in final_results}
    assert len(final_results) == 2
    connection = sqlite3.connect(tmp_path / "processing.sqlite3")
    try:
        assert connection.execute(
            "SELECT attempt_count FROM processing_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()[0] == 2
    finally:
        connection.close()


def test_callback_storage_adapter_checks_phase3_identity_without_assuming_paths():
    expected_investigation = str(uuid4())
    expected_media = str(uuid4())
    storage = StoredPhase3Media(
        {
            "investigation_id": expected_investigation,
            "media_id": expected_media,
            "filename": "image.png",
            "mime_type": "image/png",
            "size_bytes": 1,
            "checksum": "0" * 64,
            "width": 1,
            "height": 1,
            "uploaded_at": "2026-11-13T10:00:00Z",
        },
        b"x",
    )
    adapter = CallbackMediaSourceStorage(lambda _investigation, _media: storage)
    assert adapter.load_media(expected_investigation, expected_media) is storage
    mismatched = CallbackMediaSourceStorage(lambda _investigation, _media: storage)
    with pytest.raises(MediaSourceUnavailable):
        mismatched.load_media(str(uuid4()), expected_media)

    unavailable = UnavailableMediaSourceStorage()
    assert unavailable.available is False
    with pytest.raises(MediaSourceUnavailable):
        unavailable.load_media(expected_investigation, expected_media)
