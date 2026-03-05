#!/usr/bin/env python3
import argparse
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.error
import urllib.request

from dotenv import find_dotenv, load_dotenv


def load_env() -> None:
    dotenv_path = find_dotenv(usecwd=True)
    if dotenv_path:
        load_dotenv(dotenv_path, override=False)


def signed_headers(controller_id: str, shared_secret: str, method: str, path: str, query: str = "") -> dict[str, str]:
    timestamp = str(int(time.time()))
    nonce = secrets.token_hex(12)
    signing_input = "\n".join([method.upper(), path, query, timestamp, nonce])
    signature = hmac.new(shared_secret.encode("utf-8"), signing_input.encode("utf-8"), hashlib.sha256).hexdigest()
    return {
        "X-Controller-Id": controller_id,
        "X-Timestamp": timestamp,
        "X-Nonce": nonce,
        "X-Signature": signature,
    }


def http_get_json(url: str, timeout: float, headers: dict[str, str] | None = None) -> dict:
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def http_post_json(url: str, timeout: float, body: str | None = None, headers: dict[str, str] | None = None) -> dict:
    body_bytes = body.encode("utf-8") if body is not None else b""
    req = urllib.request.Request(url, data=body_bytes, headers=headers or {}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as response:
        payload = response.read().decode("utf-8")
        return json.loads(payload) if payload else {}


def main() -> int:
    load_env()

    parser = argparse.ArgumentParser(description="Open and claim ESP32 camera pairing state")
    parser.add_argument("--device-ip", required=True, help="Camera IP address")
    parser.add_argument("--http-timeout", type=float, default=5.0, help="HTTP timeout seconds")
    parser.add_argument("--status-only", action="store_true", help="Only print /pair/status")
    parser.add_argument("--open-only", action="store_true", help="Open pairing window only")

    parser.add_argument(
        "--current-controller-id",
        default=os.environ.get("GEL_CONTROLLER_ID", "gel-controller-1"),
        help="Current paired controller id (used to auth /pair/open)",
    )
    parser.add_argument(
        "--current-secret",
        default=os.environ.get("GEL_CAMERA_AUTH_SECRET", "change-me-camera-auth-secret"),
        help="Current paired shared secret (used to auth /pair/open)",
    )
    parser.add_argument(
        "--new-controller-id",
        default=None,
        help="New controller id to claim (defaults to --current-controller-id)",
    )
    parser.add_argument(
        "--new-secret",
        default=None,
        help="New shared secret to claim (defaults to --current-secret)",
    )

    args = parser.parse_args()

    new_controller_id = args.new_controller_id or args.current_controller_id
    new_secret = args.new_secret or args.current_secret

    base_url = f"http://{args.device_ip}"

    try:
        status = http_get_json(f"{base_url}/pair/status", timeout=args.http_timeout)
        print(f"Initial status: {json.dumps(status, indent=2)}")
    except Exception as exc:
        print(f"Failed to read /pair/status: {exc}")
        return 1

    if args.status_only:
        return 0

    if not status.get("pairing_open", False):
        headers = signed_headers(
            controller_id=args.current_controller_id,
            shared_secret=args.current_secret,
            method="POST",
            path="/pair/open",
        )
        try:
            opened = http_post_json(f"{base_url}/pair/open", timeout=args.http_timeout, headers=headers)
            print(f"Opened pairing: {json.dumps(opened, indent=2)}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            print(f"Failed to open pairing (HTTP {exc.code}): {detail}")
            return 1
        except Exception as exc:
            print(f"Failed to open pairing: {exc}")
            return 1
    else:
        print("Pairing already open")

    if args.open_only:
        return 0

    claim_payload = (
        '{"controller_id":"%s","shared_secret":"%s"}'
        % (new_controller_id.replace('"', "'"), new_secret.replace('"', "'"))
    )

    try:
        claimed = http_post_json(
            f"{base_url}/pair/claim",
            timeout=args.http_timeout,
            body=claim_payload,
            headers={"Content-Type": "application/json"},
        )
        print(f"Claim result: {json.dumps(claimed, indent=2)}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        print(f"Failed to claim pairing (HTTP {exc.code}): {detail}")
        return 1
    except Exception as exc:
        print(f"Failed to claim pairing: {exc}")
        return 1

    try:
        verify_headers = signed_headers(
            controller_id=new_controller_id,
            shared_secret=new_secret,
            method="GET",
            path="/props",
        )
        props = http_get_json(f"{base_url}/props", timeout=args.http_timeout, headers=verify_headers)
        print(f"Auth verification (/props): {json.dumps(props, indent=2)}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        print(f"Pairing claim succeeded but auth verify failed (HTTP {exc.code}): {detail}")
        return 1
    except Exception as exc:
        print(f"Pairing claim succeeded but auth verify failed: {exc}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
