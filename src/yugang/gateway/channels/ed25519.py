"""Ed25519 in the standard library, because a webhook has to verify signatures.

YuGang has no runtime dependencies and the cloud image has no `cryptography`
wheel, so this is the RFC 8032 algorithm written out. Two uses, both with the
same key: verify the QQ platform's `X-Signature-Ed25519` on every callback, and
sign the challenge the platform sends when a callback URL is registered.

It is not constant-time. That is acceptable here and only here: the scalar
multiplications run on the platform's own bot secret, which already travels to
the token endpoint over TLS, and the alternative was adding a compiled
dependency to a project that deliberately has none. Do not reuse this for
anything where an attacker can measure signing latency on many messages.
"""

from __future__ import annotations

import hashlib

P = 2**255 - 19
L = 2**252 + 27742317777372353535851937790883648493
D = -121665 * pow(121666, P - 2, P) % P
SQRT_M1 = pow(2, (P - 1) // 4, P)

_BX = 15112221349535400772501151409588531511454012693041857206046113283949847762202
_BY = 46316835694926478169428394003475163141307993866256225615783033603165251855960
BASE = (_BX, _BY, 1, _BX * _BY % P)


def _encode(point: tuple[int, int, int, int]) -> bytes:
    x, y, z, _t = point
    zinv = pow(z, P - 2, P)
    x = x * zinv % P
    y = y * zinv % P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _decode(data: bytes) -> tuple[int, int, int, int] | None:
    if len(data) != 32:
        return None
    value = int.from_bytes(data, "little")
    sign = value >> 255
    y = value & ((1 << 255) - 1)
    if y >= P:
        return None
    x2 = (y * y - 1) * pow(D * y * y + 1, P - 2, P) % P
    if x2 == 0:
        if sign:
            return None
        x = 0
    else:
        x = pow(x2, (P + 3) // 8, P)
        if (x * x - x2) % P != 0:
            x = x * SQRT_M1 % P
        if (x * x - x2) % P != 0:
            return None
        if (x & 1) != sign:
            x = P - x
    return (x, y, 1, x * y % P)


def _add(p: tuple[int, int, int, int], q: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    x1, y1, z1, t1 = p
    x2, y2, z2, t2 = q
    a = (y1 - x1) * (y2 - x2) % P
    b = (y1 + x1) * (y2 + x2) % P
    c = 2 * t1 * t2 * D % P
    d = 2 * z1 * z2 % P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % P, g * h % P, f * g % P, e * h % P)


def _mul(scalar: int, point: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    result = (0, 1, 1, 0)
    addend = point
    while scalar > 0:
        if scalar & 1:
            result = _add(result, addend)
        addend = _add(addend, addend)
        scalar >>= 1
    return result


def _clamp(seed: bytes) -> tuple[int, bytes]:
    digest = hashlib.sha512(seed).digest()
    scalar = int.from_bytes(digest[:32], "little")
    scalar &= (1 << 254) - 8
    scalar |= 1 << 254
    return scalar, digest[32:]


def public_key(seed: bytes) -> bytes:
    """The 32-byte public key for a 32-byte RFC 8032 seed."""
    if len(seed) != 32:
        raise ValueError("ed25519 seed must be 32 bytes")
    scalar, _prefix = _clamp(seed)
    return _encode(_mul(scalar, BASE))


def sign(seed: bytes, message: bytes) -> bytes:
    if len(seed) != 32:
        raise ValueError("ed25519 seed must be 32 bytes")
    scalar, prefix = _clamp(seed)
    public = _encode(_mul(scalar, BASE))
    nonce = int.from_bytes(hashlib.sha512(prefix + message).digest(), "little") % L
    encoded_r = _encode(_mul(nonce, BASE))
    challenge = int.from_bytes(hashlib.sha512(encoded_r + public + message).digest(), "little") % L
    s = (nonce + challenge * scalar) % L
    return encoded_r + int.to_bytes(s, 32, "little")


def verify(public: bytes, message: bytes, signature: bytes) -> bool:
    if len(public) != 32 or len(signature) != 64:
        return False
    point_a = _decode(public)
    if point_a is None:
        return False
    encoded_r, s_bytes = signature[:32], signature[32:]
    if s_bytes[31] & 224:
        return False
    s = int.from_bytes(s_bytes, "little")
    if s >= L:
        return False
    point_r = _decode(encoded_r)
    if point_r is None:
        return False
    challenge = int.from_bytes(hashlib.sha512(encoded_r + public + message).digest(), "little") % L
    return _encode(_mul(s, BASE)) == _encode(_add(point_r, _mul(challenge, point_a)))


def seed_from_secret(secret: str | bytes) -> bytes:
    """QQ's rule: repeat the bot secret until it is long enough, then take 32 bytes.

    This is the platform's own derivation (their Go SDK uses strings.Repeat then
    reads ed25519.SeedSize bytes), not something invented here.
    """
    raw = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
    if not raw:
        raise ValueError("empty secret")
    if len(raw) < 32:
        repeats = (32 + len(raw) - 1) // len(raw)
        raw = raw * repeats
    return raw[:32]
