#!/usr/bin/env python3
import argparse
import json
import sys
import time
from urllib import request, parse, error


def _req(base: str, path: str, method: str = "GET", body: dict | list | None = None, headers: dict | None = None):
    url = base.rstrip("/") + path
    data = None
    req_headers = {"Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        req_headers["Content-Type"] = "application/json"
    req = request.Request(url, data=data, headers=req_headers, method=method)
    try:
        with request.urlopen(req, timeout=30) as resp:
            content_type = resp.headers.get("Content-Type", "")
            payload = resp.read()
            if b"application/json" in content_type.encode():
                return resp.status, json.loads(payload.decode("utf-8") or "null")
            return resp.status, payload.decode("utf-8", errors="replace")
    except error.HTTPError as e:
        payload = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(payload)
        except Exception:
            return e.code, payload


def auth_headers(key: str | None, scheme: str) -> dict:
    if not key:
        return {}
    scheme = scheme.lower()
    if scheme == "bearer":
        return {"Authorization": f"Bearer {key}"}
    if scheme == "x-api-key":
        return {"X-API-Key": key}
    raise ValueError("scheme must be 'bearer' or 'x-api-key'")


def pretty(obj):
    return json.dumps(obj, indent=2, sort_keys=True, default=str)


def main():
    p = argparse.ArgumentParser(description="Smoke test for FlashCardAPI")
    p.add_argument("--base", default="http://127.0.0.1:8000", help="Base URL of the API")
    p.add_argument("--key", default=None, help="API key (optional if auth disabled)")
    p.add_argument("--scheme", default="bearer", choices=["bearer", "x-api-key"], help="Auth header scheme to use")
    p.add_argument("--limit", type=int, default=5, help="Limit for /scheduled")
    p.add_argument("--verbose", action="store_true", help="Print full JSON responses")
    args = p.parse_args()

    H = auth_headers(args.key, args.scheme)

    print(f"Base: {args.base}")
    print("1) Health check ...", end=" ")
    code, body = _req(args.base, "/health")
    ok = (code == 200 and isinstance(body, dict) and body.get("ok") is True)
    print("OK" if ok else f"FAIL ({code})")
    if not ok:
        print(pretty(body))
        sys.exit(1)

    print("2) Add a single card ...", end=" ")
    card1 = {"front": "Capital of France?", "back": "Paris"}
    code, body = _req(args.base, "/cards", method="POST", body=card1, headers=H)
    if code != 201:
        print(f"FAIL ({code})\n{pretty(body)}")
        sys.exit(1)
    print("OK")
    ids = body.get("ids", []) if isinstance(body, dict) else []

    print("3) Add multiple cards ...", end=" ")
    multi = [
        {"front": "H2O is?", "back": "Water"},
        {"front": "2+2", "back": "4"},
    ]
    code, body = _req(args.base, "/cards", method="POST", body=multi, headers=H)
    if code != 201:
        print(f"FAIL ({code})\n{pretty(body)}")
        sys.exit(1)
    print("OK")
    if isinstance(body, dict) and body.get("ids"):
        ids.extend(body["ids"])  # keep for reference

    print("4) Get scheduled ...", end=" ")
    qs = f"?{parse.urlencode({'limit': args.limit})}"
    code, body = _req(args.base, f"/scheduled{qs}", headers=H)
    if code != 200 or not isinstance(body, dict) or "cards" not in body:
        print(f"FAIL ({code})\n{pretty(body)}")
        sys.exit(1)
    print(f"OK ({body.get('count', 0)} cards)")
    if args.verbose:
        print(pretty(body))

    if body.get("count", 0) == 0 or not body["cards"]:
        print("No due cards to attempt; exiting.")
        return 0

    card_id = body["cards"][0]["id"] if isinstance(body["cards"][0], dict) else body["cards"][0].get("id")
    print(f"5) Record an attempt for {card_id} ...", end=" ")
    attempt = {"card_id": card_id, "rating": "good"}
    code, body = _req(args.base, "/attempt", method="POST", body=attempt, headers=H)
    if code != 200 or not isinstance(body, dict) or "card" not in body:
        print(f"FAIL ({code})\n{pretty(body)}")
        sys.exit(1)
    print("OK")
    if args.verbose:
        print(pretty(body))

    # Give Firestore a brief moment then fetch scheduled again
    time.sleep(0.5)
    print("6) Get scheduled again ...", end=" ")
    code, body2 = _req(args.base, f"/scheduled{qs}", headers=H)
    if code != 200:
        print(f"FAIL ({code})\n{pretty(body2)}")
        sys.exit(1)
    print(f"OK ({body2.get('count', 0)} cards)")
    if args.verbose:
        print(pretty(body2))

    print("\nSmoke test complete: SUCCESS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

