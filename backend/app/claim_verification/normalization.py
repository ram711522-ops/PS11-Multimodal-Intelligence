"""Language-neutral search normalization that never changes the stored claim."""

import unicodedata


NORMALIZATION_METHOD = "unicode_nfc_whitespace_casefold"
NORMALIZATION_VERSION = "1.0.0"


def normalize_for_search(text: str) -> str:
    """Normalize whitespace and Unicode for matching; this is not translation."""

    if not isinstance(text, str):
        raise TypeError("Claim text must be a string.")
    return " ".join(unicodedata.normalize("NFC", text).split()).casefold()
