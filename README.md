FlashCardAPI

FastAPI + Firestore HTTP API for spaced-repetition flash cards.

What Changed

- Migrated to FastAPI (`main.py`) with CORS and async request handling.
- Switched storage to Google Cloud Firestore (no local DB files).
- Kept the same endpoints and SM‑2 scheduling logic.

Endpoints

- `GET /scheduled?limit=N`
  - Returns due cards ordered by `due_at` ascending. Default `limit=20`, max 500.
  - Response: `{ cards: [ { id, front, back, created_at, easiness, interval_days, repetitions, due_at } ], count }`

- `POST /cards`
  - Adds cards. Accepts an object or array.
  - Examples:
    - `{ "front": "Capital of France?", "back": "Paris" }`
    - `[ { "front": "A", "back": "B" }, { "front": "C", "back": "D" } ]`
  - Response: `{ inserted, ids }`

- `POST /cards/csv`
  - Bulk add from CSV. Column 0 is front, column 1 is back.
  - Accepts UTF-8 `text/csv`. Optional query `skip_header=true` to skip the first row.
  - Response: `{ inserted, ids }`

- `POST /attempts`
  - Records multiple attempts in one request.
  - Body: `{ "attempts": [ { "card_id": "<id>", "rating": "good" }, { "card_id": "<id2>", "rating": 1 } ] }`
  - Response: `{ updated, cards: [ ...updated cards... ], errors: [ { card_id?, index?, error } ] }`

- `GET /health`
  - Health check. Returns `{ ok: true }`.

Rating Scale (SM‑2)

- Accepts 0..5 or strings mapped: `again -> 1`, `hard -> 3`, `good -> 4`, `easy -> 5`.
- Qualities <3 reset repetitions and set 1‑day interval; ≥3 increase interval using easiness factor (min 1.3).

Setup

Requirements: Python 3.9+.

1) Install deps

```
pip install -r requirements.txt
```

2) Configure Firestore

- Option A: Use the Firestore emulator
  - Start emulator: `gcloud beta emulators firestore start` (requires gcloud)
  - In a new shell, set env vars:
    - PowerShell: `$env:FIRESTORE_EMULATOR_HOST="localhost:8080"`
    - Bash: `export FIRESTORE_EMULATOR_HOST=localhost:8080`
  - Optionally set project: `FIRESTORE_PROJECT_ID=demo-project`

- Option B: Use a GCP project
  - Set Application Default Credentials: `gcloud auth application-default login`
  - Or set `GOOGLE_APPLICATION_CREDENTIALS` to a service account JSON
  - Optionally set `FIRESTORE_PROJECT_ID` if not embedded in credentials

Run

```
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Authentication

- This API supports API key auth suitable for OpenAI GPT “Actions”.
- Provide either header style:
  - `Authorization: Bearer <API_KEY>`
  - `X-API-Key: <API_KEY>`
- Configure one or more keys via env vars:
  - `API_KEY=yourkey` or `API_KEYS=key1,key2,key3`
- If no keys are configured, auth is disabled (useful for local dev).

GPTs Authentication Options (what GPTs support)

- No Auth: Useful for public endpoints (not recommended for write ops).
- API Key (Service-Level): Store a single API key in the GPT. Configure the OpenAPI security scheme to send it in a header (commonly `Authorization: Bearer ${API_KEY}`) or query.
- OAuth 2 (User-Level): Have end users connect their account to your API via OAuth Authorization Code with PKCE. In GPT Builder, configure `authorization_url`, `token_url`, and scopes. The GPT manages tokens and sends user tokens with each call.

Example OpenAPI security (for GPT Actions)

```yaml
components:
  securitySchemes:
    BearerAuth:
      type: apiKey
      in: header
      name: Authorization
  
security:
  - BearerAuth: []
```

When configuring the GPT Action, set Authentication to “API Key” and use a value like `Bearer ${API_KEY}` so the header becomes `Authorization: Bearer <key>`.

Examples (PowerShell)

Add cards:

```
curl -Method POST -Uri http://127.0.0.1:8000/cards -ContentType 'application/json' -Body '{"front":"Capital of France?","back":"Paris"}'
```

Get scheduled cards:

```
curl -H "Authorization: Bearer YOUR_KEY" http://127.0.0.1:8000/scheduled?limit=10
```

Record attempt (good):

```
curl -Method POST -Uri http://127.0.0.1:8000/attempt -H 'Authorization: Bearer YOUR_KEY' -ContentType 'application/json' -Body '{"card_id":"<PUT_ID>","rating":"good"}'

Record multiple attempts:

```
$body = @{ attempts = @(@{card_id='<ID1>';rating='good'}, @{card_id='<ID2>';rating=1}) } | ConvertTo-Json
curl -Method POST -Uri http://127.0.0.1:8000/attempts -H 'Authorization: Bearer YOUR_KEY' -ContentType 'application/json' -Body $body

CSV bulk upload:

```
# Using curl
curl -X POST "http://127.0.0.1:8000/cards/csv?skip_header=true" \
  -H "Authorization: Bearer YOUR_KEY" \
  -H "Content-Type: text/csv" \
  --data-binary @cards.csv

# Using the helper script
python scripts/upload_csv.py --base http://127.0.0.1:8000 --key YOUR_KEY --file cards.csv --skip-header
```
```
```

Notes

- Cards are stored in the `cards` collection; attempts are stored in a per‑card `attempts` subcollection.
- For queries, we use `where("due_at", "<=", now).order_by("due_at")`; Firestore may prompt to create an index if needed.
- CORS allows all origins for easy integration with clients and GPTs.

Containerization and Cloud Run

- Build locally with Docker:

```
docker build -t flashcardapi:latest .
docker run -e PORT=8080 -p 8080:8080 flashcardapi:latest
```

- Deploy to Cloud Run using Cloud Build (recommended):

```
gcloud config set project <YOUR_PROJECT_ID>
gcloud builds submit --tag gcr.io/<YOUR_PROJECT_ID>/flashcardapi
gcloud run deploy flashcardapi \
  --image gcr.io/<YOUR_PROJECT_ID>/flashcardapi \
  --platform managed \
  --region <YOUR_REGION> \
  --allow-unauthenticated
```

Notes for Cloud Run

- Authentication: The Cloud Run service account must have access to Firestore (roles like `Datastore User` or more granular permissions).
- API keys: Set `API_KEY` or `API_KEYS` as an environment variable in the Cloud Run service configuration.

OpenAPI for GPT Builder

- Use `openapi.yaml` when adding an Action to your GPT.
- In GPT Builder, upload the file and choose Authentication:
  - API Key header `X-API-Key` (recommended), or
  - API Key header `Authorization` with value prefix `Bearer` (enter `Bearer ${API_KEY}` in the UI).
- Replace `https://YOUR_CLOUD_RUN_URL` in `openapi.yaml` with your Cloud Run service URL.

CI/CD with GitHub Actions

- Workflows are included under `.github/workflows/`:
  - `build.yml` (automatic): builds and pushes the Docker image to Artifact Registry on push to `main`.
  - `deploy.yml` (manual): deploys a chosen image tag to Cloud Run via `workflow_dispatch`.

- Configure repository Variables (Settings → Secrets and variables → Actions → Variables):
  - `GCP_PROJECT_ID`: your GCP project id
  - `GCP_REGION`: region (e.g., `us-central1`)
  - `ARTIFACT_REPO`: Artifact Registry repo name (e.g., `flashcardapi`)
  - `CLOUD_RUN_SERVICE`: Cloud Run service name (e.g., `flashcardapi`)

- Configure repository Secrets:
  - Preferred (Workload Identity Federation):
    - `GCP_WORKLOAD_IDENTITY_PROVIDER`: resource name of the provider
    - `GCP_SERVICE_ACCOUNT`: deployer service account email
  - Or Service Account Key JSON:
    - `GCP_SA_KEY`: contents of the service account JSON key
  - Optional:
    - `API_KEYS`: comma-separated API keys to set in Cloud Run on deploy

- Artifact Registry
  - Create a Docker repo: `gcloud artifacts repositories create $ARTIFACT_REPO --repository-format=docker --location=$GCP_REGION`
  - The build workflow tags images as: `${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT_ID}/${ARTIFACT_REPO}/flashcardapi:<sha>` and also `:latest` on main.

- Project detection: The app uses `google-cloud-firestore` default credentials; no env needed if running on GCP. Optionally set `FIRESTORE_PROJECT_ID`.
- Port: Cloud Run sets `PORT`; the container defaults to `8080` for local runs.
- Health: Use `GET /health` to verify the service is up.
