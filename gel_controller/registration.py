"""Controller self-registration with the GEL web application (guard-e-loo.co.uk).

Flow on first deployment:
  1. Registration config is read from environment variables.
  2. An SSH keypair is found or generated at GEL_SSH_KEY_PATH.
  3. The controller POSTs its public key + metadata to GEL_HOME_URL.
  4. A human admin approves the request in the Fleet UI.
  5. poll_for_approval() detects the approval, writes the assigned tunnel port to
     GEL_TUNNEL_ENV_PATH, and restarts the tunnel service.

On subsequent starts, if the tunnel env file already contains a port assignment,
the whole flow is skipped (idempotent).
"""

import logging
import os
import socket
import subprocess
import time
from pathlib import Path

import requests
from dotenv import find_dotenv, load_dotenv

_DOTENV_PATH = find_dotenv(usecwd=True)
if _DOTENV_PATH:
    load_dotenv(_DOTENV_PATH, override=False)

logger = logging.getLogger(__name__)

_DEFAULT_SSH_KEY_PATH = "/etc/gel-controller/ssh/id_ed25519"
_DEFAULT_TUNNEL_ENV_PATH = "/etc/gel-controller/tunnel.env"
_TUNNEL_SERVICE = "gel-reverse-tunnel.service"


def _get_config() -> dict:
    return {
        "controller_id": os.environ.get("GEL_CONTROLLER_ID", "gel-controller-1"),
        "home_url": os.environ.get("GEL_HOME_URL", "").rstrip("/"),
        "enrollment_token": os.environ.get("GEL_ENROLLMENT_TOKEN", ""),
        "ssh_key_path": Path(os.environ.get("GEL_SSH_KEY_PATH", _DEFAULT_SSH_KEY_PATH)),
        "tunnel_env_path": Path(os.environ.get("GEL_TUNNEL_ENV_PATH", _DEFAULT_TUNNEL_ENV_PATH)),
    }


def _ensure_ssh_keypair(key_path: Path) -> str:
    """Return the SSH public key string, generating the keypair if needed."""
    pub_path = key_path.with_suffix(".pub")
    if not key_path.exists():
        key_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        logger.info("Generating new ED25519 SSH keypair at %s", key_path)
        subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(key_path)],
            check=True,
            capture_output=True,
        )
    return pub_path.read_text().strip()


def is_already_approved(tunnel_env_path: Path) -> bool:
    """Return True if the tunnel env file already contains a port assignment."""
    if not tunnel_env_path.exists():
        return False
    return "REMOTE_PORT=" in tunnel_env_path.read_text()


def register(rooms: list[str] | None = None, capabilities: list[str] | None = None) -> bool:
    """POST registration payload to GEL.

    Returns True on success (pending or already approved), False on error.
    Re-registration is safe — the server upserts the record.
    """
    cfg = _get_config()

    if not cfg["home_url"]:
        logger.warning("GEL_HOME_URL not set — skipping registration")
        return False
    if not cfg["enrollment_token"]:
        logger.warning("GEL_ENROLLMENT_TOKEN not set — skipping registration")
        return False

    try:
        pub_key = _ensure_ssh_keypair(cfg["ssh_key_path"])
    except Exception as exc:
        logger.error("Could not read/generate SSH keypair: %s", exc)
        return False

    payload = {
        "controller_id": cfg["controller_id"],
        "ssh_public_key": pub_key,
        "enrollment_token": cfg["enrollment_token"],
        "hostname": socket.getfqdn(),
        "rooms": rooms or [],
        "capabilities": capabilities or [],
    }

    url = f"{cfg['home_url']}/api/v1/controllers/register"
    try:
        resp = requests.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        logger.info("Registration response: status=%s  message=%s",
                    data.get("status"), data.get("message"))
        return True
    except requests.RequestException as exc:
        logger.error("Registration request failed: %s", exc)
        return False


def poll_for_approval(timeout: float = 1800.0, interval: float = 30.0) -> bool:
    """Block until the controller's registration is approved (or timeout elapsed).

    When approved:
      - Writes the assigned port to GEL_TUNNEL_ENV_PATH.
      - Restarts the tunnel systemd service.

    Returns True on approval, False on timeout or permanent rejection/revocation.
    """
    cfg = _get_config()
    if not cfg["home_url"]:
        return False

    controller_id = cfg["controller_id"]
    url = f"{cfg['home_url']}/api/v1/controllers/{controller_id}/status"
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            status = data.get("status")

            if status == "approved":
                tunnel_port = data.get("tunnel_port")
                if tunnel_port:
                    _write_tunnel_env(cfg["tunnel_env_path"], int(tunnel_port))
                    _restart_tunnel_service()
                    logger.info("Controller approved — tunnel port %d assigned", tunnel_port)
                    return True
                logger.error("Approved but tunnel_port missing from response")
                return False

            if status in ("rejected", "revoked"):
                logger.error(
                    "Controller registration %s — manual intervention needed", status
                )
                return False

            logger.info("Registration status: %s — waiting for admin approval…", status)

        except requests.RequestException as exc:
            logger.warning("Status poll failed: %s", exc)

        time.sleep(interval)

    logger.error("Approval wait timed out after %.0f seconds", timeout)
    return False


def _write_tunnel_env(path: Path, port: int) -> None:
    path.parent.mkdir(parents=True, mode=0o755, exist_ok=True)
    path.write_text(
        f"REMOTE_PORT={port}\n"
        f"SSH_BASTION_HOST=ssh.guard-e-loo.co.uk\n"
        f"SSH_BASTION_USER=gel-user-controller-1\n"
    )
    path.chmod(0o640)
    logger.info("Wrote tunnel env to %s", path)


def _restart_tunnel_service() -> None:
    try:
        result = subprocess.run(
            ["sudo", "systemctl", "restart", _TUNNEL_SERVICE],
            check=True,
            capture_output=True,
        )
        logger.info("Tunnel service restarted")
    except subprocess.CalledProcessError as exc:
        logger.warning(
            "Failed to restart tunnel service: %s", exc.stderr.decode().strip()
        )


def ensure_registered(
    rooms: list[str] | None = None,
    capabilities: list[str] | None = None,
) -> None:
    """Idempotent startup hook — call this from gel.py before room_controller.start().

    - If tunnel.env already has a port, does nothing (already approved).
    - If GEL_HOME_URL / GEL_ENROLLMENT_TOKEN are absent, does nothing (opt-out).
    - Otherwise, submits a registration request and blocks until admin approves
      (up to 30 minutes). Logs a warning but does NOT raise on failure so the
      controller can still run locally without a cloud connection.
    """
    cfg = _get_config()

    if not cfg["home_url"] or not cfg["enrollment_token"]:
        logger.info(
            "GEL registration disabled (GEL_HOME_URL or GEL_ENROLLMENT_TOKEN not set)"
        )
        return

    if is_already_approved(cfg["tunnel_env_path"]):
        logger.info("Already registered and approved — skipping registration")
        return

    logger.info("Registering controller with %s …", cfg["home_url"])
    if register(rooms=rooms, capabilities=capabilities):
        logger.info(
            "Registration submitted — waiting up to 30 min for admin approval…"
        )
        poll_for_approval()
    else:
        logger.warning(
            "Registration request failed — continuing without cloud tunnel"
        )
