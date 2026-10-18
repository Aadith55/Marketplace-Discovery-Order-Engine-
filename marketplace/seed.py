"""Seed a small synthetic catalog for local exploration."""
from sqlalchemy import select
from .main import app
from .models import Listing

SAMPLES = [
    {"title": "Mechanical Keyboard 75%", "description": "Hot-swappable mechanical keyboard with tactile switches", "category": "Electronics", "seller": "northstar_tech", "item_condition": "used", "price": "49.99", "quantity": 8},
    {"title": "Wireless Noise-Cancelling Headphones", "description": "Over-ear Bluetooth headphones with carrying case", "category": "Electronics", "seller": "sound_hub", "item_condition": "new", "price": "89.50", "quantity": 12},
    {"title": "Vintage Film Camera", "description": "35mm film camera, tested shutter and light meter", "category": "Cameras", "seller": "analog_corner", "item_condition": "used", "price": "124.00", "quantity": 3},
    {"title": "Trail Running Shoes Size 9", "description": "Lightweight trail shoes with grippy outsole", "category": "Sports", "seller": "run_repeat", "item_condition": "new", "price": "62.00", "quantity": 5},
    {"title": "Refurbished Smart Watch", "description": "Refurbished smartwatch with charging cable", "category": "Electronics", "seller": "device_cycle", "item_condition": "refurbished", "price": "71.25", "quantity": 4},
    {"title": "First Edition Sci-Fi Hardcover", "description": "Collector hardcover in good condition", "category": "Books", "seller": "paper_moon", "item_condition": "used", "price": "35.00", "quantity": 2},
]
factory = app.state.session_factory
with factory() as db:
    if db.scalar(select(Listing.id).limit(1)) is None:
        db.add_all([Listing(**item) for item in SAMPLES])
        db.commit()
        print(f"Seeded {len(SAMPLES)} sample listings")
    else:
        print("Catalog already has listings; seed skipped")
