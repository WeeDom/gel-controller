#!/usr/bin/env python3

import argparse
import json
import sys
import time
from urllib import request, error


def main() -> int:
    parser = argparse.ArgumentParser(description="Request baseline capture from running GEL controller")
    parser.add_argument("--host", default="127.0.0.1", help="Controller control API host")
    parser.add_argument("--port", type=int, default=8765, help="Controller control API port")
    parser.add_argument("--room-id", default=None, help="Optional room ID to capture baseline for")
    parser.add_argument(
        "--wait",
        action="store_true",
        help="Wait for completion and print final capture result",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=1.0,
        help="Polling interval in seconds when --wait is set",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Maximum seconds to wait for job completion when --wait is set",
    )
    args = parser.parse_args()

    payload = {}
    if args.room_id:
        payload["room_id"] = args.room_id

    url = f"http://{args.host}:{args.port}/api/v1/capture-baseline"
    data = json.dumps(payload).encode("utf-8")

    req = request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )

    try:
        with request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8")
            payload = json.loads(body) if body else {}
            print(json.dumps(payload, indent=2))

            if not args.wait:
                return 0

            job_id = payload.get("job_id")
            if not job_id:
                print("Response did not contain job_id; nothing to wait for", file=sys.stderr)
                return 1

            deadline = time.time() + max(1.0, args.timeout)
            job_url = f"http://{args.host}:{args.port}/api/v1/jobs/{job_id}"
            while True:
                if time.time() > deadline:
                    print(f"Timed out waiting for baseline job {job_id}", file=sys.stderr)
                    return 2

                with request.urlopen(job_url, timeout=10) as job_resp:
                    job_body = job_resp.read().decode("utf-8")
                    job_payload = json.loads(job_body) if job_body else {}

                status = job_payload.get("status")
                if status in {"completed", "failed"}:
                    print(json.dumps(job_payload, indent=2))
                    return 0 if status == "completed" else 1

                time.sleep(max(0.2, args.poll_interval))
    except error.HTTPError as e:
        body = e.read().decode("utf-8")
        print(body or str(e), file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Failed to reach running controller at {url}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
