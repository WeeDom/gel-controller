import hashlib
import hmac
import os
import secrets
import time
from typing import Mapping, Any
from urllib.parse import urlencode

from dotenv import find_dotenv, load_dotenv


_DOTENV_PATH = find_dotenv(usecwd=True)
if _DOTENV_PATH:
    load_dotenv(_DOTENV_PATH, override=False)


DEFAULT_CONTROLLER_ID = "gel-controller-1"
DEFAULT_CAMERA_SECRET = "change-me-camera-auth-secret"


def get_controller_id() -> str:
    return os.environ.get("GEL_CONTROLLER_ID", DEFAULT_CONTROLLER_ID)


def get_camera_secret() -> str:
    return os.environ.get("GEL_CAMERA_AUTH_SECRET", DEFAULT_CAMERA_SECRET)


def canonical_query(params: Mapping[str, Any] | None) -> str:
    if not params:
        return ""
    sorted_items = sorted((str(key), str(value)) for key, value in params.items())
    return urlencode(sorted_items)


def build_auth_headers(method: str, path: str, query: str = "") -> dict[str, str]:
    timestamp = str(int(time.time()))
    nonce = secrets.token_hex(12)
    signing_input = "\n".join([
        method.upper(),
        path,
        query,
        timestamp,
        nonce,
    ])

    secret = get_camera_secret().encode("utf-8")
    signature = hmac.new(secret, signing_input.encode("utf-8"), hashlib.sha256).hexdigest()

    return {
        "X-Controller-Id": get_controller_id(),
        "X-Timestamp": timestamp,
        "X-Nonce": nonce,
        "X-Signature": signature,
    }


def signed_url_and_headers(
    base_url: str,
    path: str,
    method: str,
    params: Mapping[str, Any] | None = None,
    extra_headers: Mapping[str, str] | None = None,
) -> tuple[str, dict[str, str]]:
    query = canonical_query(params)
    url = f"{base_url}{path}"
    if query:
        url = f"{url}?{query}"

    headers = build_auth_headers(method=method, path=path, query=query)
    if extra_headers:
        headers.update(extra_headers)
    return url, headers


def verify_auth_headers(
    method: str,
    path: str,
    query: str,
    headers: Mapping[str, str],
    max_age_seconds: int = 300,
) -> bool:
    """Verify an HMAC-SHA256 signed request from a LAN device (camera or sensor)."""
    try:
        timestamp = headers.get("X-Timestamp", "")
        nonce = headers.get("X-Nonce", "")
        signature = headers.get("X-Signature", "")
        if not (timestamp and nonce and signature):
            return False
        age = abs(int(time.time()) - int(timestamp))
        if age > max_age_seconds:
            return False
        signing_input = "\n".join([method.upper(), path, query, timestamp, nonce])
        secret = get_camera_secret().encode("utf-8")
        expected = hmac.new(secret, signing_input.encode("utf-8"), hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature.lower(), expected.lower())
    except Exception:
        return False
