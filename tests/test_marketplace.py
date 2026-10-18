from decimal import Decimal
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi.testclient import TestClient
from marketplace.main import create_app


@pytest.fixture()
def client(tmp_path):
    app = create_app(f"sqlite:///{tmp_path / 'test.db'}")
    with TestClient(app) as c:
        yield c


def listing(client, title="Vintage Camera", qty=3, price="50.00", **extra):
    payload = {"title": title, "description": "General listing description", "category": "Cameras", "seller": "seller_one", "item_condition": "used", "price": price, "quantity": qty}
    payload.update(extra)
    r = client.post('/listings', json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def test_search_filters_sort_and_pagination(client):
    listing(client, title="Vintage Camera", price="70.00")
    listing(client, title="Camera Lens", price="25.00", category="Accessories")
    listing(client, title="Trail Shoes", price="40.00", category="Sports")
    r = client.get('/listings', params={'q': 'camera', 'sort': 'price_asc'})
    assert r.status_code == 200
    assert [x['title'] for x in r.json()] == ['Camera Lens', 'Vintage Camera']
    r = client.get('/listings', params={'category': 'Sports', 'max_price': '45', 'limit': 1})
    assert [x['title'] for x in r.json()] == ['Trail Shoes']


def test_checkout_decrements_stock_and_returns_total(client):
    p = listing(client, qty=4, price='12.50')
    r = client.post('/orders', headers={'Idempotency-Key': 'checkout-test-001'}, json={'items': [{'listing_id': p['id'], 'quantity': 2}]})
    assert r.status_code == 201, r.text
    body = r.json()
    assert Decimal(body['total']) == Decimal('25.00')
    assert body['items'][0]['quantity'] == 2
    assert datetime.fromisoformat(body['created_at'].replace('Z', '+00:00')).utcoffset() is not None
    assert datetime.fromisoformat(p['created_at'].replace('Z', '+00:00')).utcoffset() is not None
    assert client.get(f"/listings/{p['id']}").json()['quantity'] == 2


def test_idempotent_retry_does_not_double_decrement(client):
    p = listing(client, qty=5)
    headers = {'Idempotency-Key': 'same-request-0001'}
    payload = {'items': [{'listing_id': p['id'], 'quantity': 2}]}
    first = client.post('/orders', headers=headers, json=payload)
    second = client.post('/orders', headers=headers, json=payload)
    assert first.status_code == second.status_code == 201
    assert first.json()['id'] == second.json()['id']
    assert client.get(f"/listings/{p['id']}").json()['quantity'] == 3


def test_idempotency_key_cannot_be_reused_for_different_payload(client):
    p1 = listing(client, title='Camera One', qty=5)
    p2 = listing(client, title='Camera Two', qty=5)
    headers = {'Idempotency-Key': 'same-key-different-payload'}
    assert client.post('/orders', headers=headers, json={'items': [{'listing_id': p1['id'], 'quantity': 1}]}).status_code == 201
    r = client.post('/orders', headers=headers, json={'items': [{'listing_id': p2['id'], 'quantity': 1}]})
    assert r.status_code == 409


def test_failed_multi_item_checkout_rolls_back_inventory(client):
    p1 = listing(client, title='Available Item', qty=5)
    p2 = listing(client, title='Limited Item', qty=1)
    r = client.post('/orders', headers={'Idempotency-Key': 'rollback-order-001'}, json={'items': [{'listing_id': p1['id'], 'quantity': 2}, {'listing_id': p2['id'], 'quantity': 2}]})
    assert r.status_code == 409
    assert client.get(f"/listings/{p1['id']}").json()['quantity'] == 5
    assert client.get(f"/listings/{p2['id']}").json()['quantity'] == 1


def test_invalid_price_and_duplicate_order_lines_rejected(client):
    bad = client.post('/listings', json={'title': 'Bad Listing', 'description': '', 'category': 'Books', 'seller': 'seller', 'item_condition': 'used', 'price': '-2', 'quantity': 1})
    assert bad.status_code == 422
    p = listing(client)
    r = client.post('/orders', headers={'Idempotency-Key': 'duplicate-lines-001'}, json={'items': [{'listing_id': p['id'], 'quantity': 1}, {'listing_id': p['id'], 'quantity': 1}]})
    assert r.status_code == 422


def test_trimmed_required_fields_cannot_become_too_short(client):
    for title, category, seller in [
        ("  a  ", "Books", "seller_one"),
        ("Valid title", " b ", "seller_one"),
        ("Valid title", "Books", " x "),
    ]:
        response = client.post('/listings', json={
            "title": title, "description": "", "category": category,
            "seller": seller, "item_condition": "used", "price": "1.00", "quantity": 1,
        })
        assert response.status_code == 422


def test_blank_search_and_literal_wildcards_are_safe(client):
    listing(client, title="100% Cotton Shirt", category="Clothing")
    listing(client, title="100 Percent Cotton Shirt", category="Clothing")
    assert client.get('/listings', params={'q': '   '}).status_code == 422
    matches = client.get('/listings', params={'q': '%'}).json()
    assert [item['title'] for item in matches] == ['100% Cotton Shirt']
    assert client.get('/listings', params={'category': '%'}).json() == []
    assert client.get('/listings', params={'q': "' OR 1=1 --"}).json() == []
    assert client.get('/listings', params={'offset': 1_000_001}).status_code == 422


def test_order_total_over_storage_limit_is_rejected_without_stock_change(client):
    p = listing(client, title="High Value Item", qty=1000, price="99999999.99")
    response = client.post(
        '/orders',
        headers={'Idempotency-Key': 'overflow-order-001'},
        json={'items': [{'listing_id': p['id'], 'quantity': 1000}]},
    )
    assert response.status_code == 422
    assert client.get(f"/listings/{p['id']}").json()['quantity'] == 1000


def test_whitespace_idempotency_key_is_rejected(client):
    p = listing(client)
    response = client.post(
        '/orders', headers={'Idempotency-Key': '        '},
        json={'items': [{'listing_id': p['id'], 'quantity': 1}]},
    )
    assert response.status_code == 422


def test_configured_admin_token_protects_listing_mutation_and_order_reads(tmp_path):
    app = create_app(f"sqlite:///{tmp_path / 'protected.db'}", admin_api_token="local-test-admin-token")
    with TestClient(app) as protected:
        payload = {
            "title": "Protected Listing", "description": "", "category": "Books",
            "seller": "seller_one", "item_condition": "new", "price": "5.00", "quantity": 2,
        }
        assert protected.post('/listings', json=payload).status_code == 401
        assert protected.post('/listings', headers={'X-Admin-Token': 'wrong-token'}, json=payload).status_code == 401
        created = protected.post('/listings', headers={'X-Admin-Token': 'local-test-admin-token'}, json=payload)
        assert created.status_code == 201
        order = protected.post(
            '/orders', headers={'Idempotency-Key': 'protected-order-001'},
            json={'items': [{'listing_id': created.json()['id'], 'quantity': 1}]},
        )
        assert order.status_code == 201
        assert protected.get(f"/orders/{order.json()['id']}").status_code == 401
        assert protected.get(
            f"/orders/{order.json()['id']}", headers={'X-Admin-Token': 'local-test-admin-token'}
        ).status_code == 200
        assert protected.get('/openapi.json').json()['paths']['/listings']['post']['security']


def test_production_mode_fails_closed_without_admin_token(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.delenv('ADMIN_API_TOKEN', raising=False)
    import pytest
    with pytest.raises(RuntimeError, match='ADMIN_API_TOKEN must be configured'):
        create_app(f"sqlite:///{tmp_path / 'production.db'}")


def test_concurrent_checkouts_never_oversell_inventory(client):
    p = listing(client, title="Limited Stock Item", qty=3, price="10.00")

    def buy(index):
        return client.post(
            '/orders', headers={'Idempotency-Key': f'concurrent-order-{index:04d}'},
            json={'items': [{'listing_id': p['id'], 'quantity': 1}]},
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(buy, range(8)))
    assert all(response.status_code in {201, 409, 503} for response in responses)
    successful = sum(response.status_code == 201 for response in responses)
    remaining = client.get(f"/listings/{p['id']}").json()['quantity']
    assert 0 <= remaining <= 3
    assert successful == 3 - remaining


def test_oversized_request_body_rejected_before_json_parsing(client):
    response = client.post('/listings', content=b'{' + (b' ' * (1_048_576 + 1)), headers={'Content-Type': 'application/json'})
    assert response.status_code == 413
    assert 'exceeds' in response.json()['detail']


def test_production_mode_rejects_short_admin_token(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('ADMIN_API_TOKEN', 'too-short')
    import pytest
    with pytest.raises(RuntimeError, match='at least 32 characters'):
        create_app(f"sqlite:///{tmp_path / 'weak-token.db'}")



def test_oversized_chunked_body_rejected_without_content_length():
    import asyncio
    from marketplace.middleware import RequestSizeLimitMiddleware

    chunks = iter([
        {"type": "http.request", "body": b"a" * 600_000, "more_body": True},
        {"type": "http.request", "body": b"b" * 500_000, "more_body": True},
        {"type": "http.request", "body": b"c", "more_body": False},
    ])
    messages = []
    app_called = False

    async def receive():
        return next(chunks)

    async def send(message):
        messages.append(message)

    async def inner_app(scope, receive, send):
        nonlocal app_called
        app_called = True

    middleware = RequestSizeLimitMiddleware(inner_app, max_bytes=1_048_576)
    scope = {"type": "http", "http_version": "1.1", "headers": [], "method": "POST", "path": "/test"}
    asyncio.run(middleware(scope, receive, send))
    assert not app_called
    assert messages[0]["status"] == 413
    assert (b"connection", b"close") in messages[0]["headers"]


def test_invalid_content_length_returns_bad_request():
    import asyncio
    from marketplace.middleware import RequestSizeLimitMiddleware

    messages = []
    async def receive():
        raise AssertionError("body should not be read for invalid Content-Length")
    async def send(message):
        messages.append(message)
    async def inner_app(scope, receive, send):
        raise AssertionError("app should not be called")

    middleware = RequestSizeLimitMiddleware(inner_app)
    scope = {"type": "http", "http_version": "1.1", "headers": [(b"content-length", b"not-a-number")], "method": "POST", "path": "/test"}
    asyncio.run(middleware(scope, receive, send))
    assert messages[0]["status"] == 400



def test_concurrent_same_idempotency_key_returns_one_order_without_false_stock_conflict(client):
    p = listing(client, title="Single Stock Item", qty=1, price="19.99")
    key = "duplicate-concurrent-order-001"
    payload = {"items": [{"listing_id": p["id"], "quantity": 1}]}

    def buy(_):
        return client.post('/orders', headers={"Idempotency-Key": key}, json=payload)

    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(buy, range(8)))

    # SQLite may temporarily return 503 under lock contention, but a retry with
    # the same key must recover the committed order instead of reporting stock loss.
    assert all(response.status_code in {201, 503} for response in responses)
    recovered = client.post('/orders', headers={"Idempotency-Key": key}, json=payload)
    assert recovered.status_code == 201, recovered.text
    order_id = recovered.json()["id"]
    assert all(response.status_code != 201 or response.json()["id"] == order_id for response in responses)
    assert client.get(f"/listings/{p['id']}").json()["quantity"] == 0
    from sqlalchemy import func, select
    from marketplace.models import Order
    with client.app.state.session_factory() as db:
        assert db.scalar(select(func.count(Order.id))) == 1



def test_production_mode_disables_anonymous_inventory_mutation(tmp_path, monkeypatch):
    token = "secure-test-token-with-at-least-32-chars"
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('ADMIN_API_TOKEN', token)
    app = create_app(f"sqlite:///{tmp_path / 'prod-checkout.db'}")
    with TestClient(app) as protected:
        payload = {
            "title": "Production Test Item", "description": "", "category": "Books",
            "seller": "seller_one", "item_condition": "new", "price": "8.00", "quantity": 2,
        }
        created = protected.post('/listings', headers={'X-Admin-Token': token}, json=payload)
        assert created.status_code == 201
        order = protected.post(
            '/orders', headers={'Idempotency-Key': 'production-checkout-01'},
            json={'items': [{'listing_id': created.json()['id'], 'quantity': 1}]},
        )
        assert order.status_code == 503
        assert protected.get(f"/listings/{created.json()['id']}").json()['quantity'] == 2
