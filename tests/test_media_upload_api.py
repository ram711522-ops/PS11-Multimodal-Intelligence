"""HTTP and transactional integration tests for Phase 3 media uploads."""

import asyncio
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import uuid

import httpx
from PIL import Image

from backend.app.config import Settings
from backend.app.database import database_connection
from backend.app.errors import ApiError
from backend.app.main import create_app
from backend.app.services.media_uploads import persist_processed_upload
from backend.app.services.multipart_uploads import ParsedImagePart


class ChunkedBody(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk


class MediaUploadApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory(
            dir=Path(__file__).resolve().parents[1]
        )
        self.root = Path(self.temp_directory.name)
        self.database_path = self.root / "media-api.sqlite3"
        self.storage_path = self.root / "stored-media"
        self.settings = Settings(
            database_path=self.database_path,
            media_storage_path=self.storage_path,
        )
        self.app = create_app(self.settings)

    def tearDown(self) -> None:
        self.temp_directory.cleanup()

    def request(self, method: str, path: str, *, json_body=None, files=None,
                content=None, headers=None, app=None) -> httpx.Response:
        target_app = app or self.app

        async def send_request() -> httpx.Response:
            async with target_app.router.lifespan_context(target_app):
                transport = httpx.ASGITransport(
                    app=target_app,
                    raise_app_exceptions=False,
                )
                async with httpx.AsyncClient(
                    transport=transport,
                    base_url="http://testserver",
                ) as client:
                    return await client.request(
                        method,
                        path,
                        json=json_body,
                        files=files,
                        content=content,
                        headers=headers,
                    )

        return asyncio.run(send_request())

    def create_investigation(self, *, input_type="image", claim_text=None) -> str:
        response = self.request(
            "POST",
            "/api/investigations",
            json_body={"input_type": input_type, "claim_text": claim_text},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["investigation_id"]

    def upload(self, investigation_id: str, content: bytes, filename="image.jpg",
               mime_type="image/jpeg") -> httpx.Response:
        return self.request(
            "POST",
            f"/api/investigations/{investigation_id}/media",
            files={"file": (filename, content, mime_type)},
        )

    def test_jpeg_png_and_webp_upload_responses_and_persistence(self) -> None:
        for image_format, extension, mime_type in (
            ("JPEG", ".jpg", "image/jpeg"),
            ("PNG", ".png", "image/png"),
            ("WEBP", ".webp", "image/webp"),
        ):
            with self.subTest(image_format=image_format):
                investigation_id = self.create_investigation()
                data = self.image_bytes(image_format, (20, 30, 40))
                response = self.upload(
                    investigation_id,
                    data,
                    filename="../../café-upload.fake",
                    mime_type="application/octet-stream",
                )
                self.assertEqual(response.status_code, 201, response.text)
                body = response.json()
                self.assertEqual(
                    set(body),
                    {
                        "investigation_id", "media_id", "filename", "mime_type",
                        "size_bytes", "checksum", "width", "height", "uploaded_at",
                    },
                )
                self.assertEqual(body["investigation_id"], investigation_id)
                self.assertEqual(uuid.UUID(body["media_id"]).version, 4)
                self.assertEqual(body["filename"], "café-upload.fake")
                self.assertEqual(body["mime_type"], mime_type)
                self.assertEqual(body["size_bytes"], len(data))
                self.assertEqual(body["checksum"], sha256(data).hexdigest())
                self.assertEqual((body["width"], body["height"]), (32, 24))
                self.assertTrue(body["uploaded_at"].endswith("Z"))

                physical_files = list(self.storage_path.iterdir())
                self.assertEqual(len(physical_files), 1)
                self.assertEqual(physical_files[0].name, body["media_id"] + extension)
                self.assertEqual(physical_files[0].read_bytes(), data)
                self.assertNotIn(body["filename"], str(physical_files[0]))

                with database_connection(self.database_path) as connection:
                    row = connection.execute(
                        "SELECT * FROM media_assets WHERE id = ?",
                        (body["media_id"],),
                    ).fetchone()
                    investigation = connection.execute(
                        "SELECT status FROM investigations WHERE id = ?",
                        (investigation_id,),
                    ).fetchone()
                    processing_runs = connection.execute(
                        "SELECT COUNT(*) FROM media_processing_runs WHERE media_id = ?",
                        (body["media_id"],),
                    ).fetchone()[0]
                self.assertEqual(row["processing_status"], "received")
                self.assertEqual(
                    json.loads(row["metadata_json"]),
                    {"original_filename": "café-upload.fake", "width": 32, "height": 24},
                )
                self.assertEqual(investigation["status"], "received")
                self.assertEqual(processing_runs, 0)
                self.assertEqual(row["checksum"], sha256(data).hexdigest())
                self.storage_path.joinpath(row["file_reference"]).read_bytes()
                self.storage_path.joinpath(row["file_reference"]).unlink()

    def test_corrupt_image_and_spoofed_extension_handling(self) -> None:
        investigation_id = self.create_investigation()
        corrupt = self.upload(investigation_id, b"not an image", "bad.png", "image/png")
        self.assert_error(corrupt, 422, "INVALID_IMAGE")
        self.assertEqual(list(self.storage_path.iterdir()), [])

        valid_png = self.image_bytes("PNG", (12, 34, 56))
        spoofed = self.upload(investigation_id, valid_png, "looks.jpeg", "image/jpeg")
        self.assertEqual(spoofed.status_code, 201, spoofed.text)
        self.assertEqual(spoofed.json()["mime_type"], "image/png")
        self.assertTrue((self.storage_path / f"{spoofed.json()['media_id']}.png").exists())

    def test_decoded_unsupported_image_format_returns_415_and_cleans_up(self) -> None:
        investigation_id = self.create_investigation()
        gif_data = BytesIO()
        Image.new("RGB", (16, 16), color=(80, 120, 160)).save(gif_data, format="GIF")

        response = self.upload(
            investigation_id,
            gif_data.getvalue(),
            filename="unsupported.gif",
            mime_type="image/gif",
        )

        self.assert_error(response, 415, "UNSUPPORTED_MEDIA_TYPE")
        with database_connection(self.database_path) as connection:
            media_count = connection.execute(
                "SELECT COUNT(*) FROM media_assets WHERE investigation_id = ?",
                (investigation_id,),
            ).fetchone()[0]
        self.assertEqual(media_count, 0)
        self.assertTrue(self.storage_path.exists())
        self.assertEqual(list(self.storage_path.iterdir()), [])

    def test_chunked_oversized_request_is_rejected_while_parsing(self) -> None:
        investigation_id = self.create_investigation()
        boundary = "chunk-boundary-ps11"
        preamble = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="large.png"\r\n'
            "Content-Type: image/png\r\n\r\n"
        ).encode()
        payload = b"x" * (10 * 1024 * 1024 + 1)
        ending = f"\r\n--{boundary}--\r\n".encode()
        chunks = [preamble, payload[:4 * 1024 * 1024], payload[4 * 1024 * 1024:8 * 1024 * 1024],
                  payload[8 * 1024 * 1024:], ending]
        response = self.request(
            "POST",
            f"/api/investigations/{investigation_id}/media",
            content=ChunkedBody(chunks),
            headers={"content-type": f"multipart/form-data; boundary={boundary}"},
        )
        self.assert_error(response, 413, "FILE_TOO_LARGE")
        self.assertFalse(self.storage_path.exists())

    def test_duplicate_and_five_image_limit(self) -> None:
        investigation_id = self.create_investigation()
        images = [self.image_bytes("PNG", (index * 20, 7, 9)) for index in range(1, 7)]
        first = self.upload(investigation_id, images[0], "one.png", "image/png")
        self.assertEqual(first.status_code, 201, first.text)
        duplicate = self.upload(investigation_id, images[0], "same.png", "image/png")
        self.assert_error(duplicate, 409, "DUPLICATE_MEDIA")
        for index, content in enumerate(images[1:5], start=2):
            accepted = self.upload(investigation_id, content, f"{index}.png", "image/png")
            self.assertEqual(accepted.status_code, 201, accepted.text)
        limited = self.upload(investigation_id, images[5], "six.png", "image/png")
        self.assert_error(limited, 409, "MEDIA_LIMIT_REACHED")
        self.assertEqual(len(list(self.storage_path.glob("*.png"))), 5)
        self.assertFalse(list(self.storage_path.glob(".ps11-upload-*.tmp")))

    def test_concurrent_uploads_never_exceed_five_assets(self) -> None:
        investigation_id = self.create_investigation()
        contents = [self.image_bytes("PNG", (index * 20, index, 99)) for index in range(1, 7)]

        async def concurrent_requests() -> list[httpx.Response]:
            async with self.app.router.lifespan_context(self.app):
                transport = httpx.ASGITransport(app=self.app, raise_app_exceptions=False)
                async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                    async def send(index: int, content: bytes) -> httpx.Response:
                        return await client.post(
                            f"/api/investigations/{investigation_id}/media",
                            files={"file": (f"{index}.png", content, "image/png")},
                        )

                    return await asyncio.gather(
                        *(send(index, content) for index, content in enumerate(contents))
                    )

        responses = asyncio.run(concurrent_requests())
        statuses = [response.status_code for response in responses]
        self.assertEqual(statuses.count(201), 5, statuses)
        self.assertEqual(statuses.count(409), 1, statuses)
        self.assertIn("MEDIA_LIMIT_REACHED", [
            response.json().get("error", {}).get("code") for response in responses
        ])
        with database_connection(self.database_path) as connection:
            total = connection.execute(
                "SELECT COUNT(*) FROM media_assets WHERE investigation_id = ?",
                (investigation_id,),
            ).fetchone()[0]
        self.assertEqual(total, 5)

    def test_invalid_unknown_locked_and_ineligible_investigations(self) -> None:
        self.assert_error(self.upload("not-a-uuid", self.image_bytes("PNG")), 400,
                          "INVALID_UPLOAD_REQUEST")
        unknown_id = str(uuid.uuid4())
        self.assert_error(self.upload(unknown_id, self.image_bytes("PNG")), 404,
                          "INVESTIGATION_NOT_FOUND")

        locked_id = self.create_investigation()
        with database_connection(self.database_path) as connection:
            connection.execute(
                "UPDATE investigations SET status = 'processing' WHERE id = ?",
                (locked_id,),
            )
            connection.commit()
        self.assert_error(self.upload(locked_id, self.image_bytes("PNG")), 409,
                          "MEDIA_UPLOAD_LOCKED")

        text_id = self.create_investigation(input_type="text", claim_text="A text claim")
        self.assert_error(self.upload(text_id, self.image_bytes("PNG")), 409,
                          "MEDIA_UPLOAD_NOT_ALLOWED")

    def test_missing_extra_multiple_and_malformed_multipart_requests(self) -> None:
        investigation_id = self.create_investigation()
        missing = self.request(
            "POST",
            f"/api/investigations/{investigation_id}/media",
            files={"other": ("x.png", self.image_bytes("PNG"), "image/png")},
        )
        self.assert_error(missing, 400, "INVALID_UPLOAD_REQUEST")

        boundary = "invalid-boundary"
        file_part = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"x.png\"\r\n"
            "Content-Type: image/png\r\n\r\n"
        ).encode() + self.image_bytes("PNG")
        extra_field_body = (
            file_part
            + f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"extra\"\r\n\r\nvalue".encode()
            + f"\r\n--{boundary}--\r\n".encode()
        )
        extra_field = self.request(
            "POST",
            f"/api/investigations/{investigation_id}/media",
            content=extra_field_body,
            headers={"content-type": f"multipart/form-data; boundary={boundary}"},
        )
        self.assert_error(extra_field, 400, "INVALID_UPLOAD_REQUEST")

        multiple_files = self.request(
            "POST",
            f"/api/investigations/{investigation_id}/media",
            files=[
                ("file", ("one.png", self.image_bytes("PNG"), "image/png")),
                ("file", ("two.png", self.image_bytes("PNG", (1, 2, 3)), "image/png")),
            ],
        )
        self.assert_error(multiple_files, 400, "INVALID_UPLOAD_REQUEST")

        body = file_part + b"\r\n--wrong--\r\n"
        malformed = self.request(
            "POST",
            f"/api/investigations/{investigation_id}/media",
            content=body,
            headers={"content-type": f"multipart/form-data; boundary={boundary}"},
        )
        self.assert_error(malformed, 400, "INVALID_UPLOAD_REQUEST")

    def test_persistence_failure_removes_moved_file_and_returns_contract_error(self) -> None:
        investigation_id = self.create_investigation()
        data = self.image_bytes("PNG", (1, 100, 200))
        with patch(
            "backend.app.services.media_uploads.insert_media_asset",
            side_effect=sqlite3.OperationalError("private database detail"),
        ):
            response = self.upload(investigation_id, data, "failure.png", "image/png")
        self.assert_error(response, 500, "MEDIA_PERSISTENCE_FAILED")
        self.assertNotIn(str(self.root), response.text)
        self.assertEqual(list(self.storage_path.iterdir()), [])
        with database_connection(self.database_path) as connection:
            count = connection.execute("SELECT COUNT(*) FROM media_assets").fetchone()[0]
            status_value = connection.execute(
                "SELECT status FROM investigations WHERE id = ?", (investigation_id,)
            ).fetchone()[0]
        self.assertEqual(count, 0)
        self.assertEqual(status_value, "awaiting_media")

    def test_persistence_and_compensating_cleanup_failure_preserve_persistence_error(self) -> None:
        investigation_id = self.create_investigation()
        data = self.image_bytes("PNG", (44, 55, 66))
        original_unlink = Path.unlink

        def fail_final_image_unlink(path: Path, *, missing_ok: bool = False) -> None:
            if path.parent == self.storage_path and path.suffix == ".png":
                raise OSError("cleanup failure detail")
            original_unlink(path, missing_ok=missing_ok)

        with (
            self.assertLogs("backend.app.services.media_uploads", level="ERROR"),
            patch(
                "backend.app.services.media_uploads.insert_media_asset",
                side_effect=sqlite3.OperationalError("private database detail"),
            ),
            patch.object(Path, "unlink", fail_final_image_unlink),
        ):
            response = self.upload(investigation_id, data, "failure.png", "image/png")
        self.assert_error(response, 500, "MEDIA_PERSISTENCE_FAILED")
        self.assertNotIn("cleanup failure detail", response.text)
        for final_file in self.storage_path.glob("*.png"):
            original_unlink(final_file, missing_ok=True)

    def test_spool_close_failure_happens_before_persistence_and_cleans_staged_image(self) -> None:
        investigation_id = self.create_investigation()
        data = self.image_bytes("PNG", (13, 25, 37))
        close_error = ApiError(
            500,
            "MEDIA_STORAGE_FAILED",
            "Unable to clean up the staged upload request.",
        )

        with patch(
            "backend.app.services.multipart_uploads.ParsedImagePart.close",
            side_effect=close_error,
        ):
            response = self.upload(investigation_id, data, "close-failure.png", "image/png")

        self.assert_error(response, 500, "MEDIA_STORAGE_FAILED")
        with database_connection(self.database_path) as connection:
            media_count = connection.execute(
                "SELECT COUNT(*) FROM media_assets WHERE investigation_id = ?",
                (investigation_id,),
            ).fetchone()[0]
            status_value = connection.execute(
                "SELECT status FROM investigations WHERE id = ?",
                (investigation_id,),
            ).fetchone()[0]
        self.assertEqual(media_count, 0)
        self.assertEqual(status_value, "awaiting_media")
        self.assertEqual(list(self.storage_path.iterdir()), [])

    def test_post_commit_spool_close_failure_cannot_change_success_response(self) -> None:
        investigation_id = self.create_investigation()
        data = self.image_bytes("PNG", (71, 82, 93))
        committed = False
        close_calls: list[str] = []
        original_persist = persist_processed_upload
        original_close = ParsedImagePart.close

        def persist_and_mark_committed(*args, **kwargs):
            nonlocal committed
            response = original_persist(*args, **kwargs)
            committed = True
            return response

        def fail_only_if_closed_after_commit(part) -> None:
            if committed:
                close_calls.append("after_commit")
                raise ApiError(
                    500,
                    "MEDIA_STORAGE_FAILED",
                    "A post-commit close failure must not change the response.",
                )
            close_calls.append("before_commit")
            original_close(part)

        with (
            patch(
                "backend.app.routes.investigations.persist_processed_upload",
                side_effect=persist_and_mark_committed,
            ),
            patch(
                "backend.app.services.multipart_uploads.ParsedImagePart.close",
                fail_only_if_closed_after_commit,
            ),
        ):
            response = self.upload(investigation_id, data, "committed.png", "image/png")

        self.assertEqual(response.status_code, 201, response.text)
        self.assertTrue(committed)
        self.assertEqual(close_calls, ["before_commit"])
        media_id = response.json()["media_id"]
        with database_connection(self.database_path) as connection:
            media_row = connection.execute(
                "SELECT id, file_reference FROM media_assets WHERE id = ?",
                (media_id,),
            ).fetchone()
        self.assertIsNotNone(media_row)
        self.assertTrue((self.storage_path / media_row["file_reference"]).is_file())

    def test_existing_uuid_destination_is_never_overwritten(self) -> None:
        investigation_id = self.create_investigation()
        collision_id = str(uuid.uuid4())
        self.storage_path.mkdir(parents=True, exist_ok=True)
        collision_path = self.storage_path / f"{collision_id}.png"
        original_bytes = b"existing destination must remain untouched"
        collision_path.write_bytes(original_bytes)

        with patch(
            "backend.app.services.media_uploads.generate_uuid",
            return_value=collision_id,
        ):
            response = self.upload(
                investigation_id,
                self.image_bytes("PNG", (4, 5, 6)),
                "collision.png",
                "image/png",
            )

        self.assert_error(response, 500, "MEDIA_STORAGE_FAILED")
        self.assertEqual(collision_path.read_bytes(), original_bytes)
        self.assertEqual(list(self.storage_path.iterdir()), [collision_path])
        with database_connection(self.database_path) as connection:
            media_count = connection.execute(
                "SELECT COUNT(*) FROM media_assets WHERE investigation_id = ?",
                (investigation_id,),
            ).fetchone()[0]
            status_value = connection.execute(
                "SELECT status FROM investigations WHERE id = ?",
                (investigation_id,),
            ).fetchone()[0]
        self.assertEqual(media_count, 0)
        self.assertEqual(status_value, "awaiting_media")

    def test_upload_is_persisted_after_database_reopen(self) -> None:
        investigation_id = self.create_investigation(input_type="text_image", claim_text="Claim")
        data = self.image_bytes("JPEG", (3, 4, 5))
        response = self.upload(investigation_id, data, "persist.jpg", "image/jpeg")
        self.assertEqual(response.status_code, 201, response.text)
        media_id = response.json()["media_id"]

        with database_connection(self.database_path) as connection:
            row = connection.execute(
                "SELECT id, investigation_id, checksum, file_reference FROM media_assets WHERE id = ?",
                (media_id,),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["investigation_id"], investigation_id)
        self.assertEqual(row["checksum"], sha256(data).hexdigest())
        self.assertTrue((self.storage_path / row["file_reference"]).is_file())

        reloaded_response = self.request(
            "GET",
            f"/api/investigations/{investigation_id}",
            app=create_app(self.settings),
        )
        self.assertEqual(reloaded_response.status_code, 200, reloaded_response.text)
        retrieval = reloaded_response.json()
        self.assertEqual(
            set(retrieval),
            {
                "investigation_id", "input_type", "status", "claim", "source_reference",
                "created_at", "updated_at", "media", "latest_job", "latest_result", "failure",
            },
        )
        self.assertEqual(len(retrieval["media"]), 1)
        self.assertEqual(retrieval["media"][0]["id"], media_id)
        self.assertEqual(retrieval["media"][0]["checksum"], sha256(data).hexdigest())
        self.assertNotIn("file_reference", retrieval["media"][0])

    @staticmethod
    def image_bytes(
        image_format: str,
        color: tuple[int, int, int] = (20, 30, 40),
    ) -> bytes:
        stream = BytesIO()
        Image.new("RGB", (32, 24), color=color).save(stream, format=image_format)
        return stream.getvalue()

    def assert_error(self, response: httpx.Response, status_code: int, code: str) -> None:
        self.assertEqual(response.status_code, status_code, response.text)
        body = response.json()
        self.assertEqual(set(body), {"error"})
        self.assertEqual(set(body["error"]), {"code", "message", "details"})
        self.assertEqual(body["error"]["code"], code)
        self.assertIsInstance(body["error"]["details"], dict)


if __name__ == "__main__":
    unittest.main()
