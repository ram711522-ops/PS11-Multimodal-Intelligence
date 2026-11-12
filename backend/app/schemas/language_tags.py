"""Well-formed BCP-47 language-tag validation and casing normalization."""

import re


_ALPHA = re.compile(r"^[A-Za-z]+$")
_DIGIT = re.compile(r"^[0-9]+$")
_ALNUM = re.compile(r"^[A-Za-z0-9]+$")
_GRANDFATHERED = {
    "art-lojban": "art-lojban",
    "cel-gaulish": "cel-gaulish",
    "en-gb-oed": "en-GB-oed",
    "i-ami": "i-ami",
    "i-bnn": "i-bnn",
    "i-default": "i-default",
    "i-enochian": "i-enochian",
    "i-hak": "i-hak",
    "i-klingon": "i-klingon",
    "i-lux": "i-lux",
    "i-mingo": "i-mingo",
    "i-navajo": "i-navajo",
    "i-pwn": "i-pwn",
    "i-tao": "i-tao",
    "i-tay": "i-tay",
    "i-tsu": "i-tsu",
    "no-bok": "no-bok",
    "no-nyn": "no-nyn",
    "sgn-be-fr": "sgn-BE-FR",
    "sgn-be-nl": "sgn-BE-NL",
    "sgn-ch-de": "sgn-CH-DE",
    "zh-guoyu": "zh-guoyu",
    "zh-hakka": "zh-hakka",
    "zh-min": "zh-min",
    "zh-min-nan": "zh-min-nan",
    "zh-xiang": "zh-xiang",
}


def _alpha(value: str) -> bool:
    return bool(_ALPHA.fullmatch(value))


def _alnum(value: str) -> bool:
    return bool(_ALNUM.fullmatch(value))


def canonicalize_language_tag(value: str) -> str:
    """Validate BCP-47 structure and normalize language/script/region casing.

    This performs syntax validation without an IANA registry dependency. It does
    not infer a language or claim that language processing is available.
    """

    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("language must be a non-empty BCP-47 language tag")

    grandfathered = _GRANDFATHERED.get(value.lower())
    if grandfathered is not None:
        return grandfathered

    parts = value.split("-")
    if any(not part or len(part) > 8 or not _alnum(part) for part in parts):
        raise ValueError("language must be a well-formed BCP-47 language tag")

    if parts[0].lower() == "x":
        if len(parts) < 2 or any(len(part) > 8 for part in parts[1:]):
            raise ValueError("private-use tags require one or more subtags")
        return "-".join(part.lower() for part in parts)

    language = parts[0]
    if not _alpha(language) or not (
        2 <= len(language) <= 3 or len(language) == 4 or 5 <= len(language) <= 8
    ):
        raise ValueError("language has an invalid primary language subtag")

    output = [language.lower()]
    index = 1

    if 2 <= len(language) <= 3:
        extlang_count = 0
        while index < len(parts) and len(parts[index]) == 3 and _alpha(parts[index]):
            output.append(parts[index].lower())
            index += 1
            extlang_count += 1
            if extlang_count == 3:
                break

    if index < len(parts) and len(parts[index]) == 4 and _alpha(parts[index]):
        output.append(parts[index].title())
        index += 1

    if index < len(parts) and (
        (len(parts[index]) == 2 and _alpha(parts[index]))
        or (len(parts[index]) == 3 and _DIGIT.fullmatch(parts[index]))
    ):
        output.append(parts[index].upper())
        index += 1

    variants: set[str] = set()
    while index < len(parts):
        part = parts[index]
        is_variant = (5 <= len(part) <= 8 and _alnum(part)) or (
            len(part) == 4 and part[0].isdigit() and _alnum(part)
        )
        if not is_variant:
            break
        lowered = part.lower()
        if lowered in variants:
            raise ValueError("language tag must not repeat variant subtags")
        variants.add(lowered)
        output.append(lowered)
        index += 1

    extensions: set[str] = set()
    while index < len(parts):
        singleton = parts[index].lower()
        if len(singleton) != 1 or singleton == "x":
            break
        if singleton in extensions:
            raise ValueError("language tag must not repeat extension singletons")
        extensions.add(singleton)
        output.append(singleton)
        index += 1
        extension_start = index
        while index < len(parts) and 2 <= len(parts[index]) <= 8:
            output.append(parts[index].lower())
            index += 1
        if index == extension_start:
            raise ValueError("extension singleton requires one or more subtags")

    if index < len(parts) and parts[index].lower() == "x":
        output.append("x")
        index += 1
        private_start = index
        while index < len(parts) and 1 <= len(parts[index]) <= 8:
            output.append(parts[index].lower())
            index += 1
        if index == private_start:
            raise ValueError("private-use suffix requires one or more subtags")

    if index != len(parts):
        raise ValueError("language contains an invalid or out-of-order subtag")
    return "-".join(output)
