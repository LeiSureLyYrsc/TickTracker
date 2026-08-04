"""WebAuthn Passkey 支持（基于 webauthn 库）"""
import base64
import json
import secrets
import time

from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    UserVerificationRequirement,
)

# 内存挑战存储（单进程即可）
_challenges: dict[str, dict] = {}

CHALLENGE_TTL = 300


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64u(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _new_challenge() -> bytes:
    return secrets.token_bytes(32)


def _store_challenge(challenge: bytes, **meta) -> None:
    _challenges[_b64u(challenge)] = {**meta, "exp": time.time() + CHALLENGE_TTL}


def _get_challenge(challenge: bytes) -> dict | None:
    c = _challenges.get(_b64u(challenge))
    if not c:
        return None
    if time.time() > c["exp"]:
        _challenges.pop(_b64u(challenge), None)
        return None
    return c


def _pop_challenge(challenge: bytes) -> dict | None:
    key = _b64u(challenge)
    c = _challenges.get(key)
    if c:
        _challenges.pop(key, None)
    return c


def _serialize_creation_options(o) -> dict:
    return {
        "rp": {"name": o.rp.name, "id": o.rp.id},
        "user": {
            "name": o.user.name,
            "displayName": o.user.display_name,
            "id": _b64u(o.user.id),
        },
        "challenge": _b64u(o.challenge),
        "pubKeyCredParams": [
            {"alg": p.alg, "type": "public-key"} for p in o.pub_key_cred_params
        ],
        "timeout": o.timeout,
        "attestation": o.attestation,
        "excludeCredentials": [
            {"id": _b64u(c.id), "type": "public-key"} for c in o.exclude_credentials
        ],
    }


def _serialize_request_options(o) -> dict:
    return {
        "challenge": _b64u(o.challenge),
        "timeout": o.timeout,
        "rpId": o.rp_id,
        "allowCredentials": [
            {"id": _b64u(c.id), "type": "public-key"} for c in o.allow_credentials
        ],
        "userVerification": o.user_verification,
    }


def registration_options(
    rp_id: str,
    rp_name: str,
    user_name: str,
    user_handle_b64: str,
    exclude_credential_ids: list[str],
) -> dict:
    options = generate_registration_options(
        rp_id=rp_id,
        rp_name=rp_name,
        user_name=user_name,
        user_id=_unb64u(user_handle_b64),
        user_display_name=user_name,
        challenge=_new_challenge(),
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=_unb64u(cid))
            for cid in exclude_credential_ids
        ],
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key="preferred",
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
    )
    _store_challenge(options.challenge, purpose="register", rp_id=rp_id)
    return _serialize_creation_options(options)


def verify_registration(
    credential: dict, expected_challenge: bytes, rp_id: str, origin: str
) -> dict:
    meta = _pop_challenge(expected_challenge)
    if not meta or meta.get("purpose") != "register":
        raise ValueError("挑战无效或已过期")
    result = verify_registration_response(
        credential=credential,
        expected_challenge=expected_challenge,
        expected_rp_id=rp_id,
        expected_origin=origin,
    )
    return {
        "credential_id": _b64u(result.credential_id),
        "public_key": _b64u(result.credential_public_key),
        "sign_count": result.sign_count,
    }


def authentication_options(rp_id: str, allow_credential_ids: list[str]) -> dict:
    options = generate_authentication_options(
        rp_id=rp_id,
        challenge=_new_challenge(),
        allow_credentials=[
            PublicKeyCredentialDescriptor(id=_unb64u(cid))
            for cid in allow_credential_ids
        ],
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    _store_challenge(options.challenge, purpose="auth", rp_id=rp_id)
    return _serialize_request_options(options)


def verify_authentication(
    credential: dict,
    expected_challenge: bytes,
    rp_id: str,
    origin: str,
    public_key_b64: str,
    current_sign_count: int,
) -> tuple[int, str]:
    meta = _pop_challenge(expected_challenge)
    if not meta or meta.get("purpose") != "auth":
        raise ValueError("挑战无效或已过期")
    result = verify_authentication_response(
        credential=credential,
        expected_challenge=expected_challenge,
        expected_rp_id=rp_id,
        expected_origin=origin,
        credential_public_key=_unb64u(public_key_b64),
        credential_current_sign_count=current_sign_count,
    )
    return result.new_sign_count, _b64u(result.credential_id)
