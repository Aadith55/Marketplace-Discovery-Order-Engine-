#!/usr/bin/env bash
set -euo pipefail
BASE=${BASE_URL:-http://127.0.0.1:8000}
curl -s "$BASE/health"; echo
curl -s "$BASE/listings?q=camera&sort=price_asc"; echo
curl -s -X POST "$BASE/orders" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: example-checkout-0001' \
  -d '{"items":[{"listing_id":1,"quantity":1}]}'
echo
