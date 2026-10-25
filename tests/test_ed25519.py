from __future__ import annotations

import pytest

from yugang.gateway.channels import ed25519

# RFC 8032 section 7.1, vector 1.
RFC_SEED = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
RFC_PUBLIC = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
RFC_MESSAGE = b""
RFC_SIGNATURE = bytes.fromhex(
    "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
    "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"
)


def test_rfc8032_vector_one_matches_byte_for_byte():
    assert ed25519.public_key(RFC_SEED) == RFC_PUBLIC
    assert ed25519.sign(RFC_SEED, RFC_MESSAGE) == RFC_SIGNATURE
    assert ed25519.verify(RFC_PUBLIC, RFC_MESSAGE, RFC_SIGNATURE) is True


def test_rfc8032_vector_two_signs_a_one_byte_message():
    seed = bytes.fromhex("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb")
    public = bytes.fromhex("3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c")
    message = bytes.fromhex("72")
    signature = ed25519.sign(seed, message)
    assert ed25519.public_key(seed) == public
    assert ed25519.verify(public, message, signature) is True
    assert signature.hex().startswith("92a009a9f0d4cab8")


def test_a_tampered_message_does_not_verify():
    signature = ed25519.sign(RFC_SEED, b"hello")
    public = ed25519.public_key(RFC_SEED)
    assert ed25519.verify(public, b"hello", signature) is True
    assert ed25519.verify(public, b"hellp", signature) is False


def test_a_wrong_length_signature_is_rejected_rather_than_raising():
    public = ed25519.public_key(RFC_SEED)
    assert ed25519.verify(public, b"hello", b"\x00" * 63) is False
    assert ed25519.verify(public, b"hello", b"") is False


def test_seed_from_secret_repeats_the_secret_up_to_32_bytes():
    # The QQ platform repeats the bot secret until it is at least 32 bytes and
    # keeps the first 32. Documented at bot.q.qq.com/wiki sign.html.
    secret = "naOC0ocQE3shWLAfffVLB1rhYPG7"
    assert ed25519.seed_from_secret(secret) == b"naOC0ocQE3shWLAfffVLB1rhYPG7naOC"


def test_seed_from_secret_honours_a_secret_that_is_already_long_enough():
    secret = "x" * 40
    assert ed25519.seed_from_secret(secret) == b"x" * 32


def test_seed_from_an_empty_secret_is_refused():
    with pytest.raises(ValueError):
        ed25519.seed_from_secret("")


def test_the_qq_documentation_conformance_vector_holds():
    # The wiki publishes this exact seed -> public key pair; if our curve code
    # drifts, this is the byte that catches it before a real webhook does.
    seed = ed25519.seed_from_secret("naOC0ocQE3shWLAfffVLB1rhYPG7")
    assert ed25519.public_key(seed).hex() == (
        "d7c362fe78aef81ff23287b493628b5db02a3c4fe30b215e4d19609b5d76673a"
    )
