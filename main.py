from datetime import datetime, timedelta, timezone
import os
from typing import Any, Dict, List, Union, Literal

from fastapi import FastAPI, HTTPException, Header, Depends, Body
from fastapi.middleware.cors import CORSMiddleware
from google.cloud import firestore
from pydantic import BaseModel
import io
import csv


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def rating_to_quality(rating: Any) -> int:
    if isinstance(rating, int):
        if 0 <= rating <= 5:
            return rating
        raise ValueError("rating must be 0..5")
    if isinstance(rating, str):
        r = rating.strip().lower()
        mapping = {"again": 1, "repeat": 1, "hard": 3, "ok": 4, "good": 4, "easy": 5}
        if r in mapping:
            return mapping[r]
        try:
            val = int(r)
            if 0 <= val <= 5:
                return val
        except ValueError:
            pass
    raise ValueError("Invalid rating; use 0..5 or one of: again, hard, good, easy")


def sm2_update(easiness: float, repetitions: int, interval_days: int, quality: int):
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


def get_firestore_client() -> firestore.Client:
    project_id = os.environ.get("FIRESTORE_PROJECT_ID")
    # If using emulator, Client() will pick up FIRESTORE_EMULATOR_HOST automatically.
    # If using ADC or a JSON key, set GOOGLE_APPLICATION_CREDENTIALS or run `gcloud auth application-default login`.
    return firestore.Client(project=project_id) if project_id else firestore.Client()


def ensure_dt(val: Any) -> datetime:
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        # Attempt to parse common formats
        try:
            from datetime import datetime as _dt
            if val.endswith("Z"):
                val = val.replace("Z", "+00:00")
            return _dt.fromisoformat(val)
        except Exception:
            pass
    # Fallback: now
    return utc_now()


class Card(BaseModel):
    id: str
    front: str
    back: str
    created_at: datetime
    easiness: float
    interval_days: int
    repetitions: int
    due_at: datetime


class CardCreate(BaseModel):
    front: str
    back: str


class ScheduledResponse(BaseModel):
    cards: List[Card]
    count: int


class InsertResponse(BaseModel):
    inserted: int
    ids: List[str]


class CardResponse(BaseModel):
    card: Card


def card_doc_to_model(doc: firestore.DocumentSnapshot) -> Card:
    d = doc.to_dict() or {}
    return Card(
        id=doc.id,
        front=d.get("front", ""),
        back=d.get("back", ""),
        created_at=ensure_dt(d.get("created_at", utc_now())),
        easiness=float(d.get("easiness", 2.5)),
        interval_days=int(d.get("interval_days", 0)),
        repetitions=int(d.get("repetitions", 0)),
        due_at=ensure_dt(d.get("due_at", utc_now())),
    )


app = FastAPI(title="FlashCardAPI", version="0.2-fastapi-firestore")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"]
)


@app.get("/health")
def health():
    return {"ok": True}


def _load_api_keys() -> set[str]:
    raw = os.environ.get("API_KEY", "") or os.environ.get("API_KEYS", "")
    keys = {k.strip() for k in raw.split(",") if k.strip()}
    return keys


API_KEYS = _load_api_keys()


def require_auth(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, convert_underscores=False),
):
    # If no API keys configured, auth is disabled (useful for local dev)
    if not API_KEYS:
        return

    provided: str | None = None
    if authorization:
        parts = authorization.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            provided = parts[1]
    if not provided and x_api_key:
        provided = x_api_key.strip()

    if not provided or provided not in API_KEYS:
        # Hint acceptable schemes
        raise HTTPException(status_code=401, detail="Unauthorized: provide 'Authorization: Bearer <API_KEY>' or 'X-API-Key' header")


@app.get("/scheduled", response_model=ScheduledResponse)
def get_scheduled(limit: int = 20, _auth=Depends(require_auth)):
    limit = max(1, min(500, limit))
    client = get_firestore_client()
    cards_ref = client.collection("cards")
    now = utc_now()
    # due_at <= now, ordered by due_at asc
    query = cards_ref.where("due_at", "<=", now).order_by("due_at").limit(limit)
    docs = list(query.stream())
    cards = [card_doc_to_model(doc) for doc in docs]
    return ScheduledResponse(cards=cards, count=len(cards))


@app.post("/cards", status_code=201, response_model=InsertResponse)
async def add_cards(payload: Union[CardCreate, List[CardCreate]], _auth=Depends(require_auth)):
    items: List[CardCreate] = payload if isinstance(payload, list) else [payload]
    normalized: List[Dict[str, str]] = []
    for idx, it in enumerate(items, start=1):
        front = (it.front or "").strip()
        back = (it.back or "").strip()
        if not front or not back:
            raise HTTPException(status_code=400, detail=f"Item {idx} requires 'front' and 'back'")
        normalized.append({"front": front, "back": back})

    client = get_firestore_client()
    batch = client.batch()
    now = utc_now()
    ids: List[str] = []
    for it in normalized:
        doc_ref = client.collection("cards").document()  # auto-id
        ids.append(doc_ref.id)
        batch.set(
            doc_ref,
            {
                "front": it["front"],
                "back": it["back"],
                "created_at": now,
                "easiness": 2.5,
                "interval_days": 0,
                "repetitions": 0,
                "due_at": now,
            },
        )
    batch.commit()
    return InsertResponse(inserted=len(ids), ids=ids)

class AttemptBatchItem(BaseModel):
    card_id: str
    rating: Union[int, Literal["again", "hard", "ok", "good", "easy"]]


class AttemptBatchRequest(BaseModel):
    attempts: List[AttemptBatchItem]


class AttemptBatchError(BaseModel):
    card_id: str | None = None
    index: int | None = None
    error: str


class AttemptBatchResponse(BaseModel):
    updated: int
    cards: List[Card]
    errors: List[AttemptBatchError]


@app.post("/attempts", response_model=AttemptBatchResponse)
async def record_attempts(req: AttemptBatchRequest, _auth=Depends(require_auth)):
    if not req.attempts:
        raise HTTPException(status_code=400, detail="'attempts' must be a non-empty array")

    grouped: Dict[str, List[int]] = {}
    errors: List[AttemptBatchError] = []

    for idx, item in enumerate(req.attempts, start=1):
        cid = item.card_id.strip()
        if not cid:
            errors.append(AttemptBatchError(index=idx, error="Missing card_id"))
            continue
        try:
            q = rating_to_quality(item.rating)
        except ValueError as e:
            errors.append(AttemptBatchError(card_id=cid, index=idx, error=str(e)))
            continue
        grouped.setdefault(cid, []).append(q)

    if not grouped and errors:
        return AttemptBatchResponse(updated=0, cards=[], errors=errors)

    client = get_firestore_client()
    batch = client.batch()
    now = utc_now()
    updated_ids: List[str] = []

    for cid, qualities in grouped.items():
        doc_ref = client.collection("cards").document(cid)
        snap = doc_ref.get()
        if not snap.exists:
            errors.append(AttemptBatchError(card_id=cid, error="Card not found"))
            continue
        data = snap.to_dict() or {}
        easiness = float(data.get("easiness", 2.5))
        repetitions = int(data.get("repetitions", 0))
        interval_days = int(data.get("interval_days", 0))

        for q in qualities:
            easiness, repetitions, interval_days = sm2_update(easiness, repetitions, interval_days, q)

        due = now + timedelta(days=interval_days)

        batch.update(
            doc_ref,
            {
                "easiness": easiness,
                "repetitions": repetitions,
                "interval_days": interval_days,
                "due_at": due,
            },
        )

        for q in qualities:
            attempts_ref = doc_ref.collection("attempts").document()
            batch.set(attempts_ref, {"rating": q, "timestamp": now})

        updated_ids.append(cid)

    if updated_ids:
        batch.commit()

    cards: List[Card] = []
    for cid in updated_ids:
        s = client.collection("cards").document(cid).get()
        if s.exists:
            cards.append(card_doc_to_model(s))

    return AttemptBatchResponse(updated=len(updated_ids), cards=cards, errors=errors)


@app.post("/cards/csv", status_code=201, response_model=InsertResponse)
async def add_cards_csv(
    data: bytes = Body(..., media_type="text/csv"),
    skip_header: bool = False,
    _auth=Depends(require_auth),
):
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="CSV must be UTF-8 encoded")

    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if skip_header and rows:
        rows = rows[1:]

    normalized: List[Dict[str, str]] = []
    for idx, row in enumerate(rows, start=1):
        if not row or len(row) < 2:
            # ignore short/blank rows
            continue
        front = (row[0] or "").strip()
        back = (row[1] or "").strip()
        if not front or not back:
            # skip empty values
            continue
        normalized.append({"front": front, "back": back})

    if not normalized:
        raise HTTPException(status_code=400, detail="No valid rows found (need at least two columns: front, back)")

    client = get_firestore_client()
    batch = client.batch()
    now = utc_now()
    ids: List[str] = []
    for it in normalized:
        doc_ref = client.collection("cards").document()  # auto-id
        ids.append(doc_ref.id)
        batch.set(
            doc_ref,
            {
                "front": it["front"],
                "back": it["back"],
                "created_at": now,
                "easiness": 2.5,
                "interval_days": 0,
                "repetitions": 0,
                "due_at": now,
            },
        )
    batch.commit()
    return InsertResponse(inserted=len(ids), ids=ids)
