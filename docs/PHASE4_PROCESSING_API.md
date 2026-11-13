# Phase 4 — Processing API Contract v1.0

**Status: implemented in the provisional member-2 backend; cross-branch approval and media-storage integration remain pending.**

This document describes the separately versioned API boundary for Phase 4
processing jobs and results. It does not change the frozen Shared API Contract
v1.2 or `PHASE3_MEDIA_UPLOAD.md`. The provisional member-2 backend implements
these routes and persistent job/result tables. Ram's media storage is not
present in this checkout, and cross-branch agreement remains necessary before
the frontend client is enabled.

## Versioning and common rules

- Base path: `/api/v1`.
- `api_version` is the string `1.0` in each response envelope.
- `job_id`, `investigation_id`, `media_id`, and `result_id` are UUID strings.
- All timestamps are UTC ISO 8601 timestamps ending in `Z`; optional lifecycle
  timestamps are `null` until set.
- Status values are exactly `received`, `queued`, `processing`, `completed`,
  `failed`, and `skipped`.
- `failure` is either `null` or `{ "code": string, "message": string,
  "occurred_at": UTC timestamp }`. Messages must be safe for API display.
- `result_data` is a JSON object produced by the named algorithm. Results must
  preserve `algorithm` and `algorithm_version`; the API must not invent a
  verdict, evidence item, confidence, or result when processing did not produce
  one.
- Processing progress is represented by the server-reported lifecycle status.
  This version does not define percentage or stage-level progress.
- All API errors use the existing envelope shape:

```json
{
  "error": {
    "code": "ERROR_CODE",
    "message": "Safe human-readable explanation.",
    "details": {}
  }
}
```

## Status transitions

| Current status | Allowed next status | Trigger |
|---|---|---|
| `received` | `queued`, `failed`, `skipped` | Queue accepted, scheduling failed, or explicit skip. |
| `queued` | `processing`, `failed`, `skipped` | Worker starts, scheduling fails, or explicit skip. |
| `processing` | `completed`, `failed` | All configured stages finish or a terminal processing error occurs. |
| `failed` | `queued` | Explicit retry request only. |
| `completed` | — | Terminal. A new run uses a new job. |
| `skipped` | — | Terminal. |

The retry operation reuses the job ID and represents a new attempt. A backend
may expose attempt history in a later compatible response extension; v1.0 does
not require an attempt count. Successful stage results from a failed attempt
remain retrievable, and retry failures must not delete prior result rows. A
status transition must not be inferred from client time or rendered as a
numeric completion percentage.

`skipped` is a persisted terminal lifecycle state for internal policy/worker
decisions. This v1.0 HTTP surface does not expose a public skip endpoint.

## 1. Create a processing job

```http
POST /api/v1/processing-jobs
Content-Type: application/json
```

Request (`ProcessingJobCreateRequest`):

```json
{
  "investigation_id": "255424f1-2e46-48b0-a457-53a5f0b26e60",
  "media_id": "7b28b64d-7a3f-4c88-a648-61e881ab15b2"
}
```

The backend must verify the investigation and media association, then load the
original bytes from private storage. The client does not submit a filesystem
path or re-upload bytes to this endpoint.

Success: `202 Accepted`, `Location: /api/v1/processing-jobs/{job_id}`.
Response (`ProcessingJobResponse`):

```json
{
  "api_version": "1.0",
  "job": {
    "job_id": "96d4f41c-6ea2-4cf1-9b16-f28b0e55eb02",
    "investigation_id": "255424f1-2e46-48b0-a457-53a5f0b26e60",
    "media_id": "7b28b64d-7a3f-4c88-a648-61e881ab15b2",
    "processing_status": "queued",
    "created_at": "2026-11-13T09:30:00Z",
    "started_at": null,
    "completed_at": null,
    "failure": null
  }
}
```

## 2. Retrieve processing status

```http
GET /api/v1/processing-jobs/{job_id}
```

Success: `200 OK`, using the `ProcessingJobResponse` envelope above. The
response's `job` is the current server state. Clients should poll only under a
backend-approved interval; v1.0 does not define push updates.

## 3. Retrieve processing results

```http
GET /api/v1/processing-jobs/{job_id}/results
```

Success: `200 OK`, including only actual persisted result records. An empty
`items` array is valid when no result has been produced yet or the job failed
before producing one.

```json
{
  "api_version": "1.0",
  "job_id": "96d4f41c-6ea2-4cf1-9b16-f28b0e55eb02",
  "items": [
    {
      "result_id": "<UUID>",
      "job_id": "<UUID>",
      "media_id": "<UUID>",
      "algorithm": "<registered algorithm>",
      "algorithm_version": "<version>",
      "result_type": "<result type>",
      "result_data": {},
      "created_at": "<UTC timestamp>"
    }
  ],
  "total": 1
}
```

The placeholders illustrate the schema only. They are not project results.
The result schema does not define claim-verification verdicts; those require
explicit `Evidence` inputs through the AI verification interface.

## 4. Retry a failed job

```http
POST /api/v1/processing-jobs/{job_id}/retry
Content-Type: application/json
```

Request (`ProcessingJobRetryRequest`):

```json
{ "retry_failed": true }
```

Only a `failed` job may be retried. The request must explicitly set
`retry_failed` to `true`. A retry reuses the job ID, preserves its original
creation time, records a new server-side attempt, and moves the job to `queued`
when accepted. Success is `202 Accepted` with `ProcessingJobResponse`.

## Error codes for proposed processing routes

| HTTP | Code | Meaning |
|---:|---|---|
| 400 | `INVALID_PROCESSING_REQUEST` | Malformed UUID in a path parameter. |
| 404 | `INVESTIGATION_NOT_FOUND` | Investigation UUID does not exist. |
| 404 | `PROCESSING_JOB_NOT_FOUND` | Job UUID does not exist. |
| 409 | `PROCESSING_RETRY_NOT_ALLOWED` | The job is not in `failed` state. |
| 422 | `VALIDATION_ERROR` | Request body fields, including UUIDs, failed schema validation. |
| 503 | `PROCESSING_API_UNAVAILABLE` | Processing capability is not enabled or cannot accept work. |
| 500 | `INTERNAL_SERVER_ERROR` | Unexpected failure; internal paths and exception details are not returned. |

These are proposed additions for the new `/api/v1` processing routes only. They
do not change Phase 2/3 error mappings.

The synchronous error table is distinct from `job.failure`. Processing failures
may include `MEDIA_SOURCE_UNAVAILABLE`, `PROCESSING_INPUT_INVALID`,
`PROCESSING_PERSISTENCE_FAILED`, and safe codes returned by the existing AI
workflow such as `INVALID_IMAGE` or `PHASH_FAILED`.

Because the current backend has no media table or approved storage adapter,
the job API cannot synchronously validate a media UUID. Once a backend storage
adapter is configured, a missing/unreadable source is recorded as a safe job
failure (`MEDIA_SOURCE_UNAVAILABLE`) after acceptance, rather than being
misreported as an HTTP 404. A deployment with no storage adapter rejects job
creation with `PROCESSING_API_UNAVAILABLE`.

## Integration dependencies and current behavior

The member-2 provisional backend now implements the separately versioned
`/api/v1` job, status, result, and retry routes with additive SQLite job/result
tables. Phase 3's `media_assets` table and upload storage are absent from this
checkout, so original-byte loading remains an injected dependency. The default
adapter rejects job creation; `CallbackMediaSourceStorage` provides a narrow
port for the approved storage implementation without guessing its columns or
physical filenames. The frontend client remains disabled by default and is
not called by the dashboard until the backend owner approves and configures
the media source and API integration.
