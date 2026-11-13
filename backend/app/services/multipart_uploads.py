"""Incrementally parse a single file from an ASGI multipart request."""

from dataclasses import dataclass
import logging
from tempfile import SpooledTemporaryFile
from typing import BinaryIO

from fastapi import Request
from python_multipart import MultipartParser
from python_multipart.multipart import parse_options_header
from python_multipart.exceptions import MultipartParseError

from backend.app.errors import ApiError
from backend.app.services.media_processing import MAX_UPLOAD_BYTES, READ_CHUNK_SIZE


MAX_MULTIPART_HEADER_SIZE = 16 * 1024
logger = logging.getLogger(__name__)


@dataclass
class ParsedImagePart:
    """A bounded, temporary stream for the one accepted multipart file."""

    stream: BinaryIO
    filename: str | None

    def close(self) -> None:
        try:
            self.stream.close()
        except OSError:
            logger.exception("Unable to close temporary multipart spool")
            raise ApiError(
                500,
                "MEDIA_STORAGE_FAILED",
                "Unable to clean up the staged upload request.",
            ) from None


async def receive_single_image_part(request: Request) -> ParsedImagePart:
    """Parse request chunks and count file bytes before spooling each chunk."""

    content_type = request.headers.get("content-type", "")
    try:
        media_type, parameters = parse_options_header(content_type)
    except Exception:
        raise _invalid_multipart() from None
    boundary = parameters.get(b"boundary")
    if media_type != b"multipart/form-data" or not boundary:
        raise _invalid_multipart()

    try:
        stream: BinaryIO = SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b")
    except OSError:
        logger.exception("Unable to create temporary multipart spool")
        raise ApiError(
            500,
            "MEDIA_STORAGE_FAILED",
            "Unable to stage the uploaded image.",
        ) from None
    state: dict[str, object] = {
        "part_count": 0,
        "headers": {},
        "header_name": bytearray(),
        "header_value": bytearray(),
        "file_bytes": 0,
        "filename": None,
        "has_file": False,
        "complete": False,
    }

    def on_part_begin() -> None:
        state["part_count"] = int(state["part_count"]) + 1
        if state["part_count"] != 1:
            raise _invalid_multipart()
        state["headers"] = {}

    def on_header_begin() -> None:
        state["header_name"] = bytearray()
        state["header_value"] = bytearray()

    def on_header_field(data: bytes, start: int, end: int) -> None:
        state["header_name"].extend(data[start:end])  # type: ignore[union-attr]

    def on_header_value(data: bytes, start: int, end: int) -> None:
        state["header_value"].extend(data[start:end])  # type: ignore[union-attr]

    def on_header_end() -> None:
        headers = state["headers"]
        name = bytes(state["header_name"]).strip().lower()  # type: ignore[arg-type]
        value = bytes(state["header_value"]).strip()  # type: ignore[arg-type]
        if name in headers:  # type: ignore[operator]
            raise _invalid_multipart()
        headers[name] = value  # type: ignore[index]

    def on_headers_finished() -> None:
        headers = state["headers"]
        disposition = headers.get(b"content-disposition")  # type: ignore[union-attr]
        try:
            disposition_type, options = parse_options_header(disposition or b"")
        except Exception:
            raise _invalid_multipart() from None
        if (
            disposition_type != b"form-data"
            or options.get(b"name") != b"file"
            or b"filename" not in options
        ):
            raise _invalid_multipart()
        raw_filename = options[b"filename"]
        if not isinstance(raw_filename, bytes):
            raise _invalid_multipart()
        state["filename"] = raw_filename.decode("utf-8", errors="replace")
        state["has_file"] = True

    def on_part_data(data: bytes, start: int, end: int) -> None:
        chunk = data[start:end]
        next_size = int(state["file_bytes"]) + len(chunk)
        if next_size > MAX_UPLOAD_BYTES:
            raise ApiError(413, "FILE_TOO_LARGE", "The uploaded file exceeds 10 MiB.")
        written = stream.write(chunk)
        if written != len(chunk):
            raise OSError("Incomplete multipart spool write")
        state["file_bytes"] = next_size

    def on_end() -> None:
        state["complete"] = True

    callbacks = {
        "on_part_begin": on_part_begin,
        "on_header_begin": on_header_begin,
        "on_header_field": on_header_field,
        "on_header_value": on_header_value,
        "on_header_end": on_header_end,
        "on_headers_finished": on_headers_finished,
        "on_part_data": on_part_data,
        "on_end": on_end,
    }

    try:
        parser = MultipartParser(
            boundary,
            callbacks,
            max_header_count=8,
            max_header_size=MAX_MULTIPART_HEADER_SIZE,
        )
        async for body_chunk in request.stream():
            # Slice ASGI messages so parser callbacks and file-count checks are
            # processed in bounded increments even when a server yields a large
            # message in one receive event.
            for offset in range(0, len(body_chunk), READ_CHUNK_SIZE):
                parser.write(body_chunk[offset : offset + READ_CHUNK_SIZE])
                if state["complete"]:
                    break
            if state["complete"]:
                break
        parser.finalize()
        if not state["complete"] or not state["has_file"]:
            raise _invalid_multipart()
        stream.seek(0)
        return ParsedImagePart(stream=stream, filename=state["filename"])  # type: ignore[arg-type]
    except ApiError:
        _close_spool_preserving_error(stream)
        raise
    except OSError:
        _close_spool_preserving_error(stream)
        logger.exception("Unable to write temporary multipart spool")
        raise ApiError(
            500,
            "MEDIA_STORAGE_FAILED",
            "Unable to stage the uploaded image.",
        ) from None
    except (MultipartParseError, ValueError, TypeError, KeyError):
        _close_spool_preserving_error(stream)
        raise _invalid_multipart() from None
    except Exception:
        _close_spool_preserving_error(stream)
        logger.exception("Unexpected multipart parser failure")
        raise _invalid_multipart() from None


def _invalid_multipart() -> ApiError:
    return ApiError(
        400,
        "INVALID_UPLOAD_REQUEST",
        "The multipart request must contain exactly one file in the 'file' field.",
    )


def _close_spool_preserving_error(stream: BinaryIO) -> None:
    try:
        stream.close()
    except OSError:
        logger.exception("Unable to close temporary multipart spool after request failure")
