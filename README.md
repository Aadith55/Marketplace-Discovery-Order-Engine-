# Marketplace Discovery & Order Engine

A portfolio-scale ecommerce prototype inspired by marketplace workflows. It combines a FastAPI REST API, a responsive browser UI, SQLAlchemy relational persistence, filtered product discovery, and inventory-aware demo checkout. It is not affiliated with eBay and does not process real payments.

## Features

- Search by title/description, category, condition and price; sort and paginate results.
- SQLAlchemy models for listings, orders and order-item snapshots.
- Conditional inventory decrement and transactional rollback for failed multi-item orders.
- Idempotency keys with request fingerprints so clients can safely retry a completed order.
- Request validation, bounded request bodies, database error handling, seed data and OpenAPI docs.
- Optional admin-token protection for listing creation and order reads.
- Dockerfile uses a non-root user; production mode requires an admin token and disables demo checkout until buyer authorization/payment controls exist.

## Run locally

Use Python 3.11 or newer. From the repository root, create/activate a virtual environment, run `pip install -r requirements.txt`, then `python -m marketplace.seed`, then `uvicorn marketplace.main:app --reload`. Open http://127.0.0.1:8000 for the UI or http://127.0.0.1:8000/docs for the API docs.

Run tests with `pytest -q`.

## Endpoints

- GET `/health`: service health
- GET `/listings`: filtered, sorted, paginated listings
- GET `/listings/{listing_id}`: listing and current stock
- POST `/listings`: create a listing; requires X-Admin-Token if a token is configured
- POST `/orders`: demo checkout using Idempotency-Key; disabled in production mode
- GET `/orders/{order_id}`: order summary; requires X-Admin-Token if a token is configured

## Scope and limitations

This is a demo, not a production marketplace. It does not implement buyer identity, buyer-specific authorization, payment processing, tax/shipping, rate limiting, migrations, outbox/event publication, observability or load testing. SQLite is the default; production-scale deployment would need a production database, indexed/full-text search strategy and a security review. See the project audit notes in the bundled portfolio for more context.
