#!/usr/bin/env python3
import argparse
from urllib import request, parse, error


def auth_headers(key: str | None, scheme: str) -> dict:
    if not key:
        return {}
    if scheme == "bearer":
        return {"Authorization": f"Bearer {key}"}
    if scheme == "x-api-key":
        return {"X-API-Key": key}
    raise ValueError("scheme must be 'bearer' or 'x-api-key'")


def main():
    p = argparse.ArgumentParser(description="Upload CSV to /cards/csv")
    p.add_argument("--base", default="http://127.0.0.1:8000", help="Base URL of the API")
    p.add_argument("--key", default=None, help="API key (if required)")
    p.add_argument("--scheme", default="bearer", choices=["bearer", "x-api-key"], help="Auth header scheme")
    p.add_argument("--file", required=True, help="Path to CSV file (UTF-8)")
    p.add_argument("--skip-header", action="store_true", help="Skip first row as header")
    args = p.parse_args()

    with open(args.file, "rb") as f:
        data = f.read()

    qs = "?" + parse.urlencode({"skip_header": str(args.skip_header).lower()})
    url = args.base.rstrip("/") + "/cards/csv" + qs
    headers = {"Content-Type": "text/csv"}
    headers.update(auth_headers(args.key, args.scheme))

    req = request.Request(url, data=data, headers=headers, method="POST")
    try:
        with request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            print(body)
    except error.HTTPError as e:
        print(f"HTTP {e.code}")
        print(e.read().decode("utf-8", errors="replace"))
        raise SystemExit(1)


if __name__ == "__main__":
    main()

