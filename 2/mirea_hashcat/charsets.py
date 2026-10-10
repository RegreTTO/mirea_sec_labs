"""Named and literal character sets."""

import string


CHARSETS = {
    "alnum": string.digits + string.ascii_letters,
    "digits": string.digits,
    "ascii_letters": string.ascii_letters,
    "ascii_lowercase": string.ascii_lowercase,
    "ascii_uppercase": string.ascii_uppercase,
    "hexdigits": string.hexdigits,
    "octdigits": string.octdigits,
    "punctuation": string.punctuation,
    "whitespace": string.whitespace,
    "printable": string.printable,
}


def resolve_charset(value: str) -> str:
    """Resolve exact preset names; otherwise use the literal string."""
    return "".join(dict.fromkeys(CHARSETS.get(value, value)))
