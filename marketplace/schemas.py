"""Request and response models."""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator


Condition = Literal["new", "used", "refurbished"]


class ListingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=3, max_length=160)
    description: str = Field(default="", max_length=4000)
    category: str = Field(min_length=2, max_length=80)
    seller: str = Field(min_length=2, max_length=100)
    item_condition: Condition = "used"
    price: Decimal = Field(gt=0, max_digits=10, decimal_places=2)
    quantity: int = Field(ge=1, le=100_000)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 3:
            raise ValueError("must contain at least 3 non-whitespace characters")
        return value

    @field_validator("category")
    @classmethod
    def normalize_category(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("must contain at least 2 non-whitespace characters")
        return value

    @field_validator("seller")
    @classmethod
    def normalize_seller(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("must contain at least 2 non-whitespace characters")
        return value


class ListingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    description: str
    category: str
    seller: str
    item_condition: str
    price: Decimal
    quantity: int
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def created_at_is_utc(cls, value: datetime) -> datetime:
        # SQLite drops timezone metadata for DateTime columns; normalize its
        # naive values to UTC so the API contract stays consistent across DBs.
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class OrderLineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    listing_id: int = Field(gt=0)
    quantity: int = Field(ge=1, le=1000)


class OrderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[OrderLineIn] = Field(min_length=1, max_length=50)

    @field_validator("items")
    @classmethod
    def listing_ids_must_be_unique(cls, items: list[OrderLineIn]) -> list[OrderLineIn]:
        ids = [item.listing_id for item in items]
        if len(ids) != len(set(ids)):
            raise ValueError("Each listing may appear once; combine its quantities")
        return items


class OrderItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    listing_id: int
    title_snapshot: str
    quantity: int
    unit_price: Decimal


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    status: str
    total: Decimal
    created_at: datetime
    items: list[OrderItemOut]

    @field_validator("created_at")
    @classmethod
    def created_at_is_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class HealthOut(BaseModel):
    status: str
    service: str
