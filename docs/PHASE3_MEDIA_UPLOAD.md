# PS11 Phase 3 — Media Upload Specification

**Status:** Shared Phase 3 upload interface approved by Ram and Santosh
**Applies alongside:** Frozen Shared API Contract v1.2
**Scope:** Image upload, validation, storage, persistence, and upload lifecycle

This document specifies the Phase 3 media upload interface. It supplements the frozen Shared API Contract v1.2; it does not revise that contract. It does not define analysis jobs, AI or image-authenticity analysis, evidence, or frontend behavior.

## 1. Upload endpoint

```http
POST /api/investigations/{investigation_id}/media
Content-Type: multipart/form-data
```

The request must contain exactly one file in the multipart field named `file`. Each request uploads one image. A request containing no file or an invalid upload request returns `400 INVALID_UPLOAD_REQUEST`.

The path identifier is the public investigation UUID. Malformed UUID syntax returns `400 INVALID_UPLOAD_REQUEST`. A valid UUID that does not identify an existing investigation returns `404 INVESTIGATION_NOT_FOUND`.

## 2. Successful response

An accepted upload returns `201 Created` and exactly the following nine top-level fields:

```json
{
  "investigation_id": "255424f1-2e46-48b0-a457-53a5f0b26e60",
  "media_id": "7b28b64d-7a3f-4c88-a648-61e881ab15b2",
  "filename": "image.jpg",
  "mime_type": "image/jpeg",
  "size_bytes": 245678,
  "checksum": "SHA-256 hexadecimal digest",
  "width": 1920,
  "height": 1080,
  "uploaded_at": "2026-11-13T08:15:30.123456Z"
}
```

| Field | Meaning |
|---|---|
| `investigation_id` | Public UUID of the investigation. |
| `media_id` | Server-generated public UUID of the media asset. |
| `filename` | Sanitized original uploaded filename for display. It is not used to construct a physical storage path. |
| `mime_type` | MIME type determined from validated image content. |
| `size_bytes` | Actual number of uploaded file bytes. |
| `checksum` | Hexadecimal SHA-256 digest of the original uploaded file bytes. |
| `width` | Validated image width in pixels. |
| `height` | Validated image height in pixels. |
| `uploaded_at` | UTC ISO 8601 timestamp for the accepted upload. |

The response must not contain internal storage paths or additional undocumented fields. `media_id` is public. `filename` is the sanitized original uploaded filename for display. The physical storage filename is separate and server-generated from a UUID; the original filename is never used to construct the physical storage path, and no internal path is exposed.

## 3. Accepted image content and limits

- Supported image formats: JPEG, PNG, and WebP.
- Validate decoded file content. Do not trust the client-provided filename, extension, or MIME type to establish the format.
- Maximum file size: **10 MiB**.
- Maximum width: **8192 pixels**.
- Maximum height: **8192 pixels**.
- Maximum decoded pixel count: **40 megapixels**.
- Animated WebP is rejected.
- One image is uploaded per request.
- An investigation may contain at most **five images**.

The file-size, width, height, and pixel-count limits are inclusive maxima: a file is rejected when it exceeds any limit. Unsupported formats, animated WebP, invalid image content, and dimension-limit failures use their respective errors in the error table.

## 4. Investigation eligibility and lifecycle

Only investigations with input type `image` or `text_image` may receive media. A `text` investigation cannot receive media and returns `409 MEDIA_UPLOAD_NOT_ALLOWED`.

Uploads are allowed while the investigation status is `awaiting_media` or `received`. The first successful upload changes `awaiting_media` to `received`. Additional uploads are optional, remain in `received`, and are allowed up to the five-image limit.

Uploads are locked when the investigation is in any of these states:

- `queued`
- `processing`
- `completed`
- `partial`
- `failed`

A locked upload returns `409 MEDIA_UPLOAD_LOCKED`. The upload service must allow uploads only when the investigation status is `awaiting_media` or `received`; all other statuses are rejected by the service-layer eligibility check. Upload requests do not create or queue analysis jobs and do not perform analysis.

### Existing-schema compatibility note

Retain the existing Phase 2 `investigations.status` SQLite constraint unchanged. Phase 3 must not add `queued` or `partial` to the database constraint, and no Phase 3 database migration is required for these states. Future locked states, including `queued` and `partial`, must be rejected by the service-layer upload eligibility check when introduced.

## 5. Duplicate uploads and image count

Compute SHA-256 over the original uploaded bytes. Detect duplicate content within the same investigation by checksum. A duplicate upload returns `409 DUPLICATE_MEDIA`; it does not create another media asset.

Enforce the five-image maximum per investigation. An upload that would exceed the limit returns `409 MEDIA_LIMIT_REACHED` and is not persisted.

## 6. Storage and persistence

- Configure the storage root with `PS11_MEDIA_STORAGE_PATH`.
- The default storage path is `data/media/`.
- Keep and use the existing `media_assets` SQLite table; actual file bytes must not be stored in SQLite.
- Use a separate, server-generated UUID-based physical storage filename. Never use the API display filename or any client filename to construct a filesystem path.
- Persist media metadata, including the public media UUID, investigation relationship, actual MIME type, byte size, SHA-256 checksum, processing status, and upload timestamp, in the existing table fields.
- Keep internal storage references private. They must never appear in success or error API responses.

## 7. Failure handling and cleanup

If filesystem or database work fails, remove any temporary upload file. If the file has already been moved into its final storage location but database persistence fails, remove that moved file as compensating cleanup. Return the corresponding storage or persistence error below; do not expose filesystem paths or database internals.

Compensating cleanup handles failures observed by the running request. A process crash may leave an orphaned file because filesystem and SQLite operations do not share an atomic transaction. This specification does not authorize startup cleanup or prescribe other orphan-reconciliation behavior.

## 8. Error response envelope

All upload errors use the existing standard error envelope. `details` is an object; no additional top-level response fields are defined.

```json
{
  "error": {
    "code": "ERROR_CODE",
    "message": "Human-readable message",
    "details": {}
  }
}
```

## 9. Upload error codes and HTTP statuses

These are the complete set of 13 agreed upload error codes. Do not add other upload error codes without a separately approved interface change.

| HTTP status | Error code | Meaning |
|---:|---|---|
| 400 | `INVALID_UPLOAD_REQUEST` | The investigation UUID syntax is malformed, the required file is missing, or the multipart upload request is otherwise invalid. |
| 404 | `INVESTIGATION_NOT_FOUND` | The supplied UUID is valid but no investigation exists with that identifier. |
| 409 | `MEDIA_UPLOAD_NOT_ALLOWED` | The investigation input type is not `image` or `text_image`. |
| 409 | `MEDIA_UPLOAD_LOCKED` | The investigation is in a locked lifecycle state. |
| 409 | `DUPLICATE_MEDIA` | The investigation already contains media with the same SHA-256 checksum. |
| 409 | `MEDIA_LIMIT_REACHED` | Accepting the image would exceed five images for this investigation. |
| 413 | `FILE_TOO_LARGE` | The uploaded file exceeds 10 MiB. |
| 415 | `UNSUPPORTED_MEDIA_TYPE` | The decoded image format is not JPEG, PNG, or WebP. |
| 415 | `ANIMATED_WEBP_NOT_SUPPORTED` | The uploaded WebP is animated. |
| 422 | `INVALID_IMAGE` | The file cannot be validated as a valid supported image. |
| 422 | `IMAGE_DIMENSIONS_EXCEEDED` | Width, height, or total decoded pixel count exceeds an allowed maximum. |
| 500 | `MEDIA_STORAGE_FAILED` | Writing, moving, or cleaning up the media file failed. |
| 500 | `MEDIA_PERSISTENCE_FAILED` | Persisting media metadata or the investigation status failed. |

## 10. Security and validation requirements

- Validate investigation UUID syntax and existence before accepting the upload; malformed syntax uses `400 INVALID_UPLOAD_REQUEST`, while a valid UUID with no matching investigation uses `404 INVESTIGATION_NOT_FOUND`.
- Enforce investigation type, lifecycle lock, duplicate, and image-count rules.
- Enforce the byte limit while receiving the file; do not rely only on the declared content length.
- Decode and validate actual image content and derive the returned MIME type from that validated content.
- Reject unsupported, corrupt, over-dimension, over-pixel-count, or animated WebP files using the specified error codes.
- Generate storage names on the server; do not trust user-supplied path components.
- Calculate the checksum from the exact original uploaded bytes used for duplicate detection.
- Keep internal paths, stack traces, and database details out of API responses.
- Clean up temporary and moved files after observed upload failures.
