"""Calculate password hashes using the target's salt and parameters."""

import base64
import hashlib
from collections.abc import Callable


def detect_algorithm(target: str) -> str:
    if target.startswith("$argon2"):
        return "argon2"
    if target.startswith("$2"):
        return "bcrypt"
    return "md5" if len(target) == 32 else "sha1"


def hash_md5(password: bytes, target: str = "") -> str:
    return hashlib.md5(password, usedforsecurity=False).hexdigest()


def hash_sha1(password: bytes, target: str = "") -> str:
    return hashlib.sha1(password, usedforsecurity=False).hexdigest()


def hash_bcrypt(password: bytes, target: str) -> str:
    import bcrypt

    return bcrypt.hashpw(password, target.encode("ascii")).decode("ascii")


def hash_argon2(password: bytes, target: str) -> str:
    from argon2 import extract_parameters
    from argon2.low_level import hash_secret

    parameters = extract_parameters(target)
    prefix, encoded_salt, _ = target.rsplit("$", 2)
    salt = base64.b64decode(encoded_salt + "=" * (-len(encoded_salt) % 4))
    calculated = hash_secret(
        password, salt, time_cost=parameters.time_cost,
        memory_cost=parameters.memory_cost, parallelism=parameters.parallelism,
        hash_len=parameters.hash_len, type=parameters.type, version=parameters.version,
    ).decode("ascii")
    return f"{prefix}${encoded_salt}${calculated.rsplit('$', 1)[1]}"


HASH_FUNCTIONS: dict[str, Callable[[bytes, str], str]] = {
    "md5": hash_md5,
    "sha1": hash_sha1,
    "bcrypt": hash_bcrypt,
    "argon2": hash_argon2,
}
ALGORITHMS = ("auto", *HASH_FUNCTIONS)


def hash_password(password: bytes, algorithm: str, target: str) -> str:
    return HASH_FUNCTIONS[algorithm](password, target)
