"""Explicit command-line entry point for processing an uploaded media asset."""

from dataclasses import asdict
import argparse
import json
import sys

from backend.app.config import Settings
from backend.app.database import initialize_database
from backend.app.services.media_pipeline import process_existing_uploaded_media


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--investigation-id", required=True)
    parser.add_argument("--media-id", required=True)
    parser.add_argument(
        "--frame-timestamp-ms",
        action="append",
        type=int,
        dest="frame_timestamps_ms",
        help="Optional video frame timestamp; may be repeated.",
    )
    args = parser.parse_args()
    settings = Settings.from_environment()
    initialize_database(settings.database_path)
    outcome = process_existing_uploaded_media(
        settings.database_path,
        settings.media_storage_path,
        args.investigation_id,
        args.media_id,
        requested_timestamps_ms=(
            tuple(args.frame_timestamps_ms) if args.frame_timestamps_ms is not None else None
        ),
    )
    print(json.dumps(asdict(outcome), ensure_ascii=False, indent=2))
    return 0 if outcome.status == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
