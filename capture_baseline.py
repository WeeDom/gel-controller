#!/usr/bin/env python3

import argparse
import json
import sys
from urllib import request, error


def main() -> int:
    parser = argparse.ArgumentParser(description="Request baseline capture from running GEL controller")
    parser.add_argument("--host", default="127.0.0.1", help="Controller control API host")
    parser.add_argument("--port", type=int, default=8765, help="Controller control API port")
    parser.add_argument("--room-id", default=None, help="Optional room ID to capture baseline for")
    args = parser.parse_args()

    payload = {}
    if args.room_id:
        payload["room_id"] = args.room_id

    url = f"http://{args.host}:{args.port}/capture-baseline"
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
            print(body)
            return 0
    except error.HTTPError as e:
        body = e.read().decode("utf-8")
        print(body or str(e), file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Failed to reach running controller at {url}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
