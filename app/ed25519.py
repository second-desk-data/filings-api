#!/usr/bin/env python3
"""
Pure-Python Ed25519 (RFC 8032) — verify + sign.

The filings API is stdlib-only by design (no pip in the deploy image),
and the Python standard library has no Ed25519. This module implements
the RFC 8032 algorithms directly: point arithmetic in extended
coordinates (Hisil et al. add-2008-hwcd / dbl-2008-hwcd for the a=-1
twisted Edwards curve), so verification needs a single modular
inversion (at point decoding) instead of one per point addition.

Used by app/vermarco.py to verify Vermarco's signed delivery-endpoint
callbacks. `sign` exists so the test battery can play the platform's
role end to end; production code paths only call `verify`.

Correctness is pinned by the RFC 8032 §7.1 test vectors in
tests/run_tests_v2.py.
"""

import hashlib

_Q = 2 ** 255 - 19                                        # field prime
_L = 2 ** 252 + 27742317777372353535851937790883648493    # group order
_D = (-121665 * pow(121666, _Q - 2, _Q)) % _Q             # curve constant
_I = pow(2, (_Q - 1) // 4, _Q)                            # sqrt(-1) mod q


def _sha512(data):
    return hashlib.sha512(data).digest()


def _xrecover(y):
    """Recover the even x for a y on the curve (RFC 8032 §3)."""
    xx = (y * y - 1) * pow(_D * y * y + 1, _Q - 2, _Q) % _Q
    x = pow(xx, (_Q + 3) // 8, _Q)
    if (x * x - xx) % _Q != 0:
        x = (x * _I) % _Q
    if (x * x - xx) % _Q != 0:
        raise ValueError("point is not on the curve")
    if x & 1:
        x = _Q - x
    return x


# Base point in extended coordinates (X, Y, Z, T), x = X/Z, y = Y/Z.
_B_Y = (4 * pow(5, _Q - 2, _Q)) % _Q
_B_X = _xrecover(_B_Y)
_B = (_B_X, _B_Y, 1, (_B_X * _B_Y) % _Q)
_IDENTITY = (0, 1, 1, 0)


def _point_add(P, Q):
    X1, Y1, Z1, T1 = P
    X2, Y2, Z2, T2 = Q
    A = (Y1 - X1) * (Y2 - X2) % _Q
    B = (Y1 + X1) * (Y2 + X2) % _Q
    C = 2 * _D * T1 * T2 % _Q
    D = 2 * Z1 * Z2 % _Q
    E = (B - A) % _Q
    F = (D - C) % _Q
    G = (D + C) % _Q
    H = (B + A) % _Q
    return (E * F % _Q, G * H % _Q, F * G % _Q, E * H % _Q)


def _point_double(P):
    X1, Y1, Z1, _T1 = P
    A = X1 * X1 % _Q
    B = Y1 * Y1 % _Q
    C = 2 * Z1 * Z1 % _Q
    D = (-A) % _Q
    E = ((X1 + Y1) * (X1 + Y1) - A - B) % _Q
    G = (D + B) % _Q
    F = (G - C) % _Q
    H = (D - B) % _Q
    return (E * F % _Q, G * H % _Q, F * G % _Q, E * H % _Q)


def _scalarmult(P, e):
    Q = _IDENTITY
    while e > 0:
        if e & 1:
            Q = _point_add(Q, P)
        P = _point_double(P)
        e >>= 1
    return Q


def _point_encode(P):
    X, Y, Z, _T = P
    zinv = pow(Z, _Q - 2, _Q)
    x = X * zinv % _Q
    y = Y * zinv % _Q
    bits = y | ((x & 1) << 255)
    return bits.to_bytes(32, "little")


def _point_decode(data):
    if len(data) != 32:
        raise ValueError("encoded point must be 32 bytes")
    bits = int.from_bytes(data, "little")
    y = bits & ((1 << 255) - 1)
    sign = bits >> 255
    if y >= _Q:
        raise ValueError("y coordinate out of range")
    x = _xrecover(y)
    if (x & 1) != sign:
        x = _Q - x
    if x == 0 and sign == 1:
        raise ValueError("invalid encoding: x = 0 with sign bit set")
    return (x, y, 1, (x * y) % _Q)


def _points_equal(P, Q):
    X1, Y1, Z1, _ = P
    X2, Y2, Z2, _ = Q
    return ((X1 * Z2 - X2 * Z1) % _Q == 0
            and (Y1 * Z2 - Y2 * Z1) % _Q == 0)


def _hint(data):
    return int.from_bytes(_sha512(data), "little")


def publickey(seed32):
    """Derive the 32-byte public key for a 32-byte secret seed."""
    if len(seed32) != 32:
        raise ValueError("seed must be 32 bytes")
    h = _sha512(seed32)
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= (1 << 254)
    return _point_encode(_scalarmult(_B, a))


def sign(seed32, msg):
    """Ed25519 signature (64 bytes) of msg under the seed's keypair."""
    if len(seed32) != 32:
        raise ValueError("seed must be 32 bytes")
    h = _sha512(seed32)
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= (1 << 254)
    prefix = h[32:]
    A_enc = _point_encode(_scalarmult(_B, a))
    r = _hint(prefix + msg) % _L
    R_enc = _point_encode(_scalarmult(_B, r))
    k = _hint(R_enc + A_enc + msg) % _L
    S = (r + k * a) % _L
    return R_enc + S.to_bytes(32, "little")


def verify(pubkey32, msg, signature64):
    """True iff signature64 is a valid Ed25519 signature of msg.

    Cofactorless verification per RFC 8032 §5.1.7:
    [S]B == R + [H(R || A || M)]A, with S range-checked.
    Never raises on malformed input — returns False.
    """
    try:
        if len(pubkey32) != 32 or len(signature64) != 64:
            return False
        A = _point_decode(pubkey32)
        R_enc = signature64[:32]
        R = _point_decode(R_enc)
        S = int.from_bytes(signature64[32:], "little")
        if S >= _L:
            return False
        k = _hint(R_enc + pubkey32 + msg) % _L
        return _points_equal(_scalarmult(_B, S),
                             _point_add(R, _scalarmult(A, k)))
    except (ValueError, IndexError, OverflowError):
        return False
