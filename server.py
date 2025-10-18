import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs


DB_DIR = os.path.join(os.path.dirname(__file__), "data")
DB_PATH = os.path.join(DB_DIR, "flashcards.db")


def utc_now():
    return datetime.now(timezone.utc)


def ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def parse_iso(dt_str: str) -> datetime:
    return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))


def get_conn():
    if not os.path.isdir(DB_DIR):
        os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                front TEXT NOT NULL,
                back TEXT NOT NULL,
                created_at TEXT NOT NULL,
                easiness REAL NOT NULL DEFAULT 2.5,
                interval_days INTEGER NOT NULL DEFAULT 0,
                repetitions INTEGER NOT NULL DEFAULT 0,
                due_at TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_id INTEGER NOT NULL,
                rating INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                FOREIGN KEY(card_id) REFERENCES cards(id) ON DELETE CASCADE
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def row_to_card(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "front": row["front"],
        "back": row["back"],
        "created_at": row["created_at"],
        "easiness": row["easiness"],
        "interval_days": row["interval_days"],
        "repetitions": row["repetitions"],
        "due_at": row["due_at"],
    }


def sm2_update(easiness: float, repetitions: int, interval_days: int, quality: int):
    # quality: 0..5 (0-2 fail; 3-5 pass)
    if quality < 0 or quality > 5:
        raise ValueError("quality must be 0..5")

    if quality < 3:
        repetitions_new = 0
        interval_new = 1
    else:
        repetitions_new = repetitions + 1
        if repetitions == 0:
            interval_new = 1
        elif repetitions == 1:
            interval_new = 6
        else:
            interval_new = int(round(interval_days * easiness))
            if interval_new < 1:
                interval_new = 1

    ef_new = easiness + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
    if ef_new < 1.3:
        ef_new = 1.3

    return ef_new, repetitions_new, interval_new


def rating_to_quality(rating):
    # Accept integers 0..5 or strings: again(1), hard(3), good(4), easy(5)
    if isinstance(rating, int):
        return rating
    if isinstance(rating, str):
        r = rating.strip().lower()
        mapping = {
            "again": 1,
            "repeat": 1,
            "hard": 3,
            "ok": 4,
            "good": 4,
            "easy": 5,
        }
        if r in mapping:
            return mapping[r]
        try:
            return int(r)
        except ValueError:
            pass
    raise ValueError("Invalid rating; use 0..5 or one of: again, hard, good, easy")


class FlashcardHandler(BaseHTTPRequestHandler):
    server_version = "FlashCardAPI/0.1"

    def _set_cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send_json(self, obj, status=200):
        data = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self._set_cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length > 0 else b""
        try:
            return json.loads(raw.decode("utf-8") or "null")
        except json.JSONDecodeError:
            self._send_json({"error": "Invalid JSON"}, status=400)
            return None

    def do_OPTIONS(self):
        self.send_response(204)
        self._set_cors()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/scheduled":
            self.handle_get_scheduled(parsed)
        elif parsed.path == "/health":
            self._send_json({"ok": True})
        else:
            self._send_json({"error": "Not found"}, status=404)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/cards":
            self.handle_post_cards()
        elif parsed.path == "/attempt":
            self.handle_post_attempt()
        else:
            self._send_json({"error": "Not found"}, status=404)

    def handle_get_scheduled(self, parsed):
        qs = parse_qs(parsed.query)
        limit = 20
        if "limit" in qs:
            try:
                limit = max(1, min(500, int(qs["limit"][0])))
            except ValueError:
                return self._send_json({"error": "limit must be integer"}, status=400)
        now_str = ts(utc_now())
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT * FROM cards
                WHERE due_at <= ?
                ORDER BY datetime(due_at) ASC
                LIMIT ?
                """,
                (now_str, limit),
            )
            rows = cur.fetchall()
            cards = [row_to_card(r) for r in rows]
            self._send_json({"cards": cards, "count": len(cards)})
        finally:
            conn.close()

    def handle_post_cards(self):
        body = self._read_json()
        if body is None:
            return

        items = body if isinstance(body, list) else [body]
        to_insert = []
        for i, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                return self._send_json({"error": f"Item {i} must be object"}, status=400)
            front = (item.get("front") or "").strip()
            back = (item.get("back") or "").strip()
            if not front or not back:
                return self._send_json({"error": f"Item {i} requires 'front' and 'back'"}, status=400)
            to_insert.append((front, back))

        now = ts(utc_now())
        conn = get_conn()
        ids = []
        try:
            cur = conn.cursor()
            for front, back in to_insert:
                cur.execute(
                    """
                    INSERT INTO cards(front, back, created_at, due_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (front, back, now, now),
                )
                ids.append(cur.lastrowid)
            conn.commit()
        finally:
            conn.close()

        self._send_json({"inserted": len(ids), "ids": ids}, status=201)

    def handle_post_attempt(self):
        body = self._read_json()
        if body is None:
            return
        if not isinstance(body, dict):
            return self._send_json({"error": "Body must be object"}, status=400)

        card_id = body.get("card_id")
        rating = body.get("rating")
        if card_id is None or rating is None:
            return self._send_json({"error": "Fields 'card_id' and 'rating' are required"}, status=400)

        try:
            quality = rating_to_quality(rating)
        except ValueError as e:
            return self._send_json({"error": str(e)}, status=400)

        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM cards WHERE id = ?", (card_id,))
            row = cur.fetchone()
            if not row:
                return self._send_json({"error": "Card not found"}, status=404)

            ef, reps, interval = sm2_update(
                easiness=row["easiness"],
                repetitions=row["repetitions"],
                interval_days=row["interval_days"],
                quality=quality,
            )
            due = utc_now() + timedelta(days=interval)

            cur.execute(
                """
                UPDATE cards
                SET easiness = ?, repetitions = ?, interval_days = ?, due_at = ?
                WHERE id = ?
                """,
                (ef, reps, interval, ts(due), card_id),
            )
            cur.execute(
                """
                INSERT INTO attempts(card_id, rating, timestamp)
                VALUES (?, ?, ?)
                """,
                (card_id, quality, ts(utc_now())),
            )
            conn.commit()

            # Return updated card
            cur.execute("SELECT * FROM cards WHERE id = ?", (card_id,))
            updated = cur.fetchone()
            self._send_json({"card": row_to_card(updated)})
        finally:
            conn.close()


def run(host: str = "127.0.0.1", port: int = 8000):
    init_db()
    httpd = HTTPServer((host, port), FlashcardHandler)
    sa = httpd.socket.getsockname()
    print(f"Serving on http://{sa[0]}:{sa[1]}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    h = os.environ.get("HOST", "127.0.0.1")
    try:
        p = int(os.environ.get("PORT", "8000"))
    except ValueError:
        print("Invalid PORT; falling back to 8000", file=sys.stderr)
        p = 8000
    run(h, p)

