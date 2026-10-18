"""Marketplace discovery and checkout API.

A portfolio-scale service demonstrating API design, query filtering, SQL data
modeling, inventory-safe checkout, and idempotent request handling.
"""
from contextlib import asynccontextmanager
from decimal import Decimal
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
from typing import Annotated, Literal
from hmac import compare_digest

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import APIKeyHeader
from sqlalchemy import case, func, or_, select, update
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

from .db import Base, build_session_factory
from .models import Listing, Order, OrderItem
from .middleware import RequestSizeLimitMiddleware
from .schemas import HealthOut, ListingCreate, ListingOut, OrderCreate, OrderOut

STATIC_DIR = Path(__file__).parent / "static"
DEFAULT_DB = os.getenv("DATABASE_URL", "sqlite:///./marketplace.db")
MAX_ORDER_TOTAL = Decimal("9999999999.99")  # matches Numeric(12, 2) storage
logger = logging.getLogger(__name__)


def _escape_like_literal(value: str) -> str:
    """Escape SQL LIKE metacharacters so user input is treated literally."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def create_app(database_url: str = DEFAULT_DB, admin_api_token: str | None = None) -> FastAPI:
    configured_admin_token = admin_api_token if admin_api_token is not None else os.getenv("ADMIN_API_TOKEN")
    is_production = os.getenv("APP_ENV", "development").lower() in {"production", "prod"}
    if is_production and not configured_admin_token:
        raise RuntimeError("ADMIN_API_TOKEN must be configured when APP_ENV=production")
    if is_production and len(configured_admin_token) < 32:
        raise RuntimeError("ADMIN_API_TOKEN must contain at least 32 characters when APP_ENV=production")

    engine, SessionLocal = build_session_factory(database_url)
    Base.metadata.create_all(bind=engine)

    def get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    @asynccontextmanager
    async def lifespan(_app):
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(
        lifespan=lifespan,
        title="Marketplace Discovery & Order Engine",
        version="1.0.0",
        description=(
            "A small marketplace backend demonstrating searchable listings, "
            "inventory-aware checkout, and idempotent order creation. Demo only: "
            "full user authentication/authorization, payment processing, shipping, and tax are out of scope. "
            "An optional admin token protects listing creation and order reads when configured."
        ),
    )
    app.state.engine = engine
    app.state.session_factory = SessionLocal
    app.add_middleware(RequestSizeLimitMiddleware, max_bytes=1_048_576)
    # Ensure pooled connections are returned when a TestClient/server shuts down.

    @app.exception_handler(OperationalError)
    async def handle_database_operational_error(request: Request, exc: OperationalError):
        # Never expose connection strings or low-level database messages to clients.
        logger.warning("Database operation failed on %s: %s", request.url.path, type(exc).__name__)
        return JSONResponse(
            status_code=503,
            content={"detail": "Temporary database problem; retry the request"},
            headers={"Retry-After": "1"},
        )

    DB = Annotated[Session, Depends(get_db)]

    if configured_admin_token:
        admin_key_header = APIKeyHeader(name="X-Admin-Token", auto_error=False)

        def require_admin(x_admin_token: Annotated[str | None, Depends(admin_key_header)] = None):
            if not x_admin_token or not compare_digest(x_admin_token.encode("utf-8"), configured_admin_token.encode("utf-8")):
                raise HTTPException(status_code=401, detail="Valid X-Admin-Token required")

        admin_dependencies = [Depends(require_admin)]
    else:
        # No auth in local demo mode; production mode is rejected at startup above.
        admin_dependencies = []

    @app.get("/", include_in_schema=False)
    def home():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/health", response_model=HealthOut, tags=["system"])
    def health(db: DB):
        try:
            db.execute(select(1))
        except SQLAlchemyError as exc:
            logger.warning("Marketplace health check failed: %s", type(exc).__name__)
            raise HTTPException(status_code=503, detail="Database unavailable") from exc
        return {"status": "ok", "service": "marketplace-engine"}

    @app.post("/listings", response_model=ListingOut, status_code=201, tags=["listings"], dependencies=admin_dependencies)
    def create_listing(payload: ListingCreate, db: DB):
        listing = Listing(**payload.model_dump())
        db.add(listing)
        try:
            db.commit()
            db.refresh(listing)
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(status_code=409, detail="Listing conflicts with a database constraint") from exc
        return listing

    @app.get("/listings", response_model=list[ListingOut], tags=["listings"])
    def search_listings(
        db: DB,
        q: str | None = Query(default=None, min_length=1, max_length=120),
        category: str | None = Query(default=None, max_length=80),
        min_price: Decimal | None = Query(default=None, ge=0),
        max_price: Decimal | None = Query(default=None, ge=0),
        item_condition: Literal["new", "used", "refurbished"] | None = None,
        sort: Literal["relevance", "price_asc", "newest"] = "relevance",
        offset: int = Query(default=0, ge=0, le=1_000_000),
        limit: int = Query(default=20, ge=1, le=100),
    ):
        if min_price is not None and max_price is not None and min_price > max_price:
            raise HTTPException(status_code=422, detail="min_price must not exceed max_price")
        if q is not None:
            q = q.strip()
            if not q:
                raise HTTPException(status_code=422, detail="q must not be blank")
        if category is not None:
            category = category.strip()
            if not category:
                raise HTTPException(status_code=422, detail="category must not be blank")
        stmt = select(Listing).where(Listing.quantity > 0)
        if q:
            needle = f"%{_escape_like_literal(q)}%"
            stmt = stmt.where(or_(
                Listing.title.ilike(needle, escape="\\"),
                Listing.description.ilike(needle, escape="\\"),
            ))
        if category:
            # Category is a label, not a pattern. Avoid wildcard matching here.
            stmt = stmt.where(func.lower(Listing.category) == category.lower())
        if min_price is not None:
            stmt = stmt.where(Listing.price >= min_price)
        if max_price is not None:
            stmt = stmt.where(Listing.price <= max_price)
        if item_condition:
            stmt = stmt.where(Listing.item_condition == item_condition)
        if sort == "price_asc":
            stmt = stmt.order_by(Listing.price.asc(), Listing.id.asc())
        elif sort == "newest":
            stmt = stmt.order_by(Listing.created_at.desc(), Listing.id.desc())
        elif q:
            # Exact-title/partial-title matches rank ahead of description matches.
            title_match = case((Listing.title.ilike(f"%{_escape_like_literal(q)}%", escape="\\"), 0), else_=1)
            stmt = stmt.order_by(title_match.asc(), Listing.created_at.desc(), Listing.id.desc())
        else:
            stmt = stmt.order_by(Listing.created_at.desc(), Listing.id.desc())
        return list(db.scalars(stmt.offset(offset).limit(limit)).all())

    @app.get("/listings/{listing_id}", response_model=ListingOut, tags=["listings"])
    def get_listing(listing_id: int, db: DB):
        listing = db.get(Listing, listing_id)
        if listing is None:
            raise HTTPException(status_code=404, detail="Listing not found")
        return listing

    @app.post("/orders", response_model=OrderOut, status_code=201, tags=["orders"])
    def create_order(
        payload: OrderCreate,
        db: DB,
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
    ):
        # This prototype has no buyer identity, payment authorization, or
        # per-user order permissions. Fail closed instead of exposing anonymous
        # inventory-mutating checkout in production mode.
        if is_production:
            raise HTTPException(
                status_code=503,
                detail="Demo checkout is disabled in production mode until buyer authorization and payment controls are implemented",
            )
        idempotency_key = idempotency_key.strip()
        if not 8 <= len(idempotency_key) <= 128:
            raise HTTPException(status_code=422, detail="Idempotency-Key must contain 8 to 128 non-whitespace characters")

        # Canonical fingerprint makes retries safe and detects accidental key reuse.
        normalized = sorted(
            [{"listing_id": item.listing_id, "quantity": item.quantity} for item in payload.items],
            key=lambda row: row["listing_id"],
        )
        fingerprint = sha256(json.dumps(normalized, separators=(",", ":")).encode()).hexdigest()
        existing = db.scalar(select(Order).where(Order.idempotency_key == idempotency_key))
        if existing:
            if existing.request_fingerprint != fingerprint:
                raise HTTPException(status_code=409, detail="Idempotency-Key was already used with a different order")
            return existing

        order_lines = []
        total = Decimal("0.00")
        try:
            # Calculate the complete order total before mutating inventory. This
            # prevents overflow beyond Numeric(12, 2) and avoids unnecessary writes.
            requested_ids = [item.listing_id for item in payload.items]
            listings_by_id = {
                listing.id: listing
                for listing in db.scalars(select(Listing).where(Listing.id.in_(requested_ids))).all()
            }
            for requested in payload.items:
                listing = listings_by_id.get(requested.listing_id)
                if listing is None:
                    raise HTTPException(status_code=404, detail=f"Listing {requested.listing_id} not found")
                total += Decimal(listing.price) * requested.quantity

            total = total.quantize(Decimal("0.01"))
            if total > MAX_ORDER_TOTAL:
                raise HTTPException(status_code=422, detail="Order total exceeds the supported maximum")

            # Build the order and flush its unique idempotency key before touching
            # inventory. Concurrent retries with the same key then contend on the
            # unique constraint first, rather than incorrectly losing to an
            # inventory check after the first request has already succeeded.
            for requested in sorted(payload.items, key=lambda line: line.listing_id):
                listing = listings_by_id[requested.listing_id]
                order_lines.append(
                    OrderItem(
                        listing_id=listing.id,
                        title_snapshot=listing.title,
                        quantity=requested.quantity,
                        unit_price=listing.price,
                    )
                )
            order = Order(
                idempotency_key=idempotency_key,
                request_fingerprint=fingerprint,
                total=total,
                status="confirmed",
                items=order_lines,
            )
            db.add(order)
            db.flush()  # The uniqueness claim and stock changes share one transaction.

            # Conditional updates ensure stock cannot be decremented below zero.
            # A later failure rolls back the order record and prior stock decrements.
            for requested in sorted(payload.items, key=lambda line: line.listing_id):
                result = db.execute(
                    update(Listing)
                    .where(Listing.id == requested.listing_id, Listing.quantity >= requested.quantity)
                    .values(quantity=Listing.quantity - requested.quantity)
                )
                if result.rowcount != 1:
                    raise HTTPException(status_code=409, detail=f"Insufficient stock for listing {requested.listing_id}")
            db.commit()
            db.refresh(order)
            return order
        except HTTPException:
            db.rollback()
            raise
        except IntegrityError:
            db.rollback()
            # Defensive response if concurrent callers race to reserve the same key.
            existing = db.scalar(select(Order).where(Order.idempotency_key == idempotency_key))
            if existing and existing.request_fingerprint == fingerprint:
                return existing
            raise HTTPException(status_code=409, detail="Could not confirm order; retry with the same Idempotency-Key")
        except OperationalError as exc:
            db.rollback()
            # SQLite can be temporarily locked under concurrent checkout; some
            # networked databases can also fail transiently. A retry with the
            # same key is safe even if the client did not receive the response.
            logger.warning("Transient database error during checkout: %s", type(exc).__name__)
            raise HTTPException(
                status_code=503,
                detail="Temporary database contention; retry with the same Idempotency-Key",
                headers={"Retry-After": "1"},
            ) from exc
        except Exception:
            db.rollback()
            raise

    @app.get("/orders/{order_id}", response_model=OrderOut, tags=["orders"], dependencies=admin_dependencies)
    def get_order(order_id: int, db: DB):
        order = db.scalar(select(Order).where(Order.id == order_id))
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")
        return order

    return app


app = create_app()
