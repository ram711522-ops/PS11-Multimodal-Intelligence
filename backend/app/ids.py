"""Identifier helpers shared by future persistence services."""

from uuid import uuid4


def generate_uuid() -> str:
    """Return a new UUID v4 string suitable for database primary keys."""

    return str(uuid4())
