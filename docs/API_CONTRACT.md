# PS11 Shared API Contract v1.2

**Status:** Investigation API contract
**Base prefix:** `/api`
**Backend:** FastAPI
**Persistence:** SQLite

This contract defines investigation creation and retrieval. Investigation and media records are stored in SQLite using internal database column `id`; public API responses call the investigation identifier `investigation_id`. Image upload, analysis, evidence, graph, propagation, and realtime endpoints remain deferred until their phases and shared contracts are approved.

## Conventions

- Public identifiers are server-generated UUID strings.
- Timestamps are UTC ISO 8601 strings ending in `Z`.
- Request and response bodies use JSON (`application/json`).
- Errors use `{ "error": { "code": "...", "message": "...", "details": {} } }`.
- `language` accepts exactly `en`, `en-US`, `en-GB`, `hi`, `hi-IN`, `te`, and `te-IN` after BCP-47 syntax and casing normalization. A well-formed but unsupported tag returns HTTP 422 with `LANGUAGE_NOT_SUPPORTED`; a malformed tag returns HTTP 422 with `INVALID_LANGUAGE_TAG`. Missing or `null` means automatic-detection mode and is stored as `null`. This metadata contract does not mean language detection, translation, or language processing is implemented.

## Create an investigation

### Request

`POST /api/investigations`

```json
{
  "input_type": "text",
  "claim_text": "A fictional claim submitted for investigation.",
  "source_reference": "https://example.org/report",
  "language": "en"
}
```

| Field | Type | Rules |
|---|---|---|
| `input_type` | string | Required; one of `text`, `image`, or `text_image`. |
| `claim_text` | string or null | `text` and `text_image` require non-empty text. `image` requires `null` (omitting the field is treated as null). Maximum 10,000 characters. |
| `source_reference` | string or null | Optional valid HTTP or HTTPS URL; maximum 2,048 characters. |
| `language` | string or null | Optional BCP-47 tag from the exact allowlist: `en`, `en-US`, `en-GB`, `hi`, `hi-IN`, `te`, or `te-IN`. Missing/null is stored as null; no language detector runs in this phase. |

### Success response

`201 Created`

The creation response contains exactly these public fields:

```json
{
  "investigation_id": "255424f1-2e46-48b0-a457-53a5f0b26e60",
  "input_type": "text",
  "status": "received",
  "created_at": "2026-11-13T08:15:30.123456Z",
  "message": "Investigation created successfully."
}
```

Initial status is `received` for `text` and `awaiting_media` for `image` and `text_image`.

## Retrieve one investigation

### Request

`GET /api/investigations/{investigation_id}`

### Success response

`200 OK`

```json
{
  "investigation_id": "255424f1-2e46-48b0-a457-53a5f0b26e60",
  "input_type": "text",
  "status": "received",
  "claim": {
    "text": "A fictional claim submitted for investigation.",
    "language": "en",
    "normalized_text": null
  },
  "source_reference": "https://example.org/report",
  "created_at": "2026-11-13T08:15:30.123456Z",
  "updated_at": "2026-11-13T08:15:30.123456Z",
  "media": [],
  "latest_job": null,
  "latest_result": null,
  "failure": null
}
```

The response contains exactly these 11 fields. For a text-bearing investigation, `claim` is an object with `text`, `language` (or `null` when unavailable), and `normalized_text`. `normalized_text` is initially `null`; it must not copy the submitted text and is populated only when actual normalization is implemented. `claim` is `null` for image-only investigations. `media` is an empty list before media is uploaded, and `latest_job`, `latest_result`, and `failure` are `null` until their respective future phases are implemented. This phase does not create job, result, or failure objects. An unknown UUID returns `404 Not Found`; invalid UUID syntax returns `422 Unprocessable Entity`.

## Retrieve investigation history

### Request

`GET /api/investigations?limit=20&offset=0`

- Default `limit`: 20; minimum: 1; maximum: 100.
- Default `offset`: 0; minimum: 0.
- Ordering is deterministic: `created_at DESC, investigation_id DESC`.

### Success response

`200 OK`

```json
{
  "items": [
    {
      "investigation_id": "255424f1-2e46-48b0-a457-53a5f0b26e60",
      "input_type": "text",
      "status": "received",
      "claim": {
        "text": "A fictional claim submitted for investigation.",
        "language": "en",
        "normalized_text": null
      },
      "source_reference": "https://example.org/report",
      "created_at": "2026-11-13T08:15:30.123456Z",
      "updated_at": "2026-11-13T08:15:30.123456Z"
    }
  ],
  "total": 1,
  "limit": 20,
  "offset": 0
}
```

Every history item uses `investigation_id`; no public `id` field or cursor is returned. `total` is the number of matching investigations before pagination.

## Investigation statuses

| Status | Meaning |
|---|---|
| `awaiting_media` | An image or text-plus-image investigation is persisted and awaits the later media-upload phase. |
| `received` | A text investigation is persisted, or a future media phase has accepted required media. It does not imply analysis ran. |
| `processing` | An actual future analysis task has started. |
| `completed` | The selected implemented analysis tasks completed. |
| `failed` | Processing failed. |

Phase 1 transition behavior: text creation starts at `received`; image and text-plus-image creation start at `awaiting_media`. Upload-driven and analysis-driven transitions are deferred.

## Standard errors and HTTP status codes

Error response:

```json
{
  "error": {
    "code": "validation_error",
    "message": "Request validation failed.",
    "details": {
      "fields": [
        {
          "field": "body.claim_text",
          "issue": "claim_text is required and must not be blank"
        }
      ]
    }
  }
}
```

`details` is always a JSON object. Error messages must not expose stack traces, internal filesystem paths, or database details.

| Code | Use |
|---:|---|
| `200 OK` | Investigation or history retrieved. |
| `201 Created` | Investigation persisted. |
| `404 Not Found` | Investigation UUID does not exist. |
| `422 Unprocessable Entity` | Invalid request fields, claim, URL, UUID syntax, or pagination parameter. |
| `422 Unprocessable Entity` with `INVALID_LANGUAGE_TAG` | The language value is not a well-formed BCP-47 tag. |
| `422 Unprocessable Entity` with `LANGUAGE_NOT_SUPPORTED` | The tag is well-formed but is not one of `en`, `en-US`, `en-GB`, `hi`, `hi-IN`, `te`, or `te-IN`. |
| `500 Internal Server Error` | Unexpected database or server failure, returned without internal details. |

## Deferred API scope

The previously proposed media upload, historical index, evidence, controlled feed events, graph, results, and WebSocket endpoints are not implemented by this contract revision. They require their respective phase review and shared API agreement before implementation.
