# Marketplace Discovery & Order Engine

A portfolio-scale marketplace prototype inspired by common ecommerce workflows. It combines a REST API, a small browser UI, relational persistence, filtered search, and inventory-aware checkout. It is not affiliated with eBay and does not process real payments.

## Why this project is relevant

- **Product discovery:** query, category, condition, price-range, sort, and pagination filters; title matches rank ahead of description-only matches.
- **Order integrity:** checkout validates quantities and conditionally decrements stock inside a database transaction. Failed multi-item checkout rolls back earlier inventory changes.
- **Idempotency:** an `Idempotency-Key` lets a client retry a successful order without creating a second order or decrementing stock twice. Reusing a key for a different payload returns HTTP 409.
- **Engineering practice:** FastAPI/OpenAPI contract, SQLAlchemy data model, request validation, bounded 1 MiB request bodies, safe database error responses, Docker, seed data, and regression tests.
- **Operational guardrails:** production mode requires an admin token of at least 32 characters; SQLite connection timeouts and foreign-key enforcement are enabled; browser checkout stores bounded per-payload idempotency keys in session storage so retries can reuse the same key after a network error or page refresh when the same cart is rebuilt.
- **End-to-end demo:** a lightweight responsive browser UI talks to the same REST API for search and checkout.

## Run locally

Python 3.11+ is recommended.

```bash
python -m venv .venv
# macOS/Linux: source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m marketplace.seed
uvicorn marketplace.main:app --reload
```

Open `http://127.0.0.1:8000` for the UI, `http://127.0.0.1:8000/docs` for interactive API docs, and `http://127.0.0.1:8000/health` for health status.

## Tests

From this directory:

```bash
pytest -q
```

The 19 tests cover search/sort/filtering, literal wildcard handling, whitespace normalization, successful and concurrent checkout, retry idempotency, idempotency-key conflicts, rollback, total overflow, admin-token behavior, production startup guards and disabled production checkout, and oversized request rejection.

## Docker

For a local-only demo without the admin token:

```bash
docker build -t marketplace-engine .
docker run --rm -e APP_ENV=development -p 127.0.0.1:8000:8000 marketplace-engine
```

The image runs as a non-root user and writes its SQLite database under `/data`. The container defaults to `APP_ENV=production` and refuses to start unless `ADMIN_API_TOKEN` is at least 32 characters. For a protected run, supply a strong token through an environment variable or secret manager:

```bash
docker run --rm -e ADMIN_API_TOKEN="$ADMIN_API_TOKEN" -p 127.0.0.1:8000:8000 marketplace-engine
```

When configured, `X-Admin-Token` protects listing creation and order reads. Local development has authentication disabled unless a token is configured. Demo checkout works in development only; production mode disables order creation until buyer authentication, per-user authorization, and payment controls are implemented. Do not expose this prototype as a real marketplace. The service uses SQLite by default; set `DATABASE_URL` to a PostgreSQL SQLAlchemy URL when running against PostgreSQL (install the appropriate PostgreSQL driver first).

## Main endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/health` | Service health |
| POST | `/listings` | Create a listing (requires `X-Admin-Token` when configured) |
| GET | `/listings` | Search/filter/sort/paginate active listings |
| GET | `/listings/{listing_id}` | Get listing and current stock |
| POST | `/orders` | Check out using `Idempotency-Key` header |
| GET | `/orders/{order_id}` | Fetch order summary (requires `X-Admin-Token` when configured) |

## Data model

- `listings`: listing attributes, seller, category, condition, price, and available quantity.
- `orders`: confirmed purchase, total, idempotency key, request fingerprint, and status.
- `order_items`: immutable title/price snapshots and purchased quantities.

## Deliberate scope and next steps

This is a demo, not a production marketplace. Token protection is a small admin guard, not a substitute for user identity, authorization policy, secret rotation, or a full security review. Payment authorization, tax and shipping calculation, seller reputation, outbox/event publication, metrics/tracing, rate limiting, database migrations, and load testing are not implemented. Substring search uses leading-wildcard `LIKE` predicates and may need a full-text/trigram index or search service at scale. A natural next step would be an outbox-backed order event and a separate search index consumer.
