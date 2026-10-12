# VIKMO DealerDesk — AI / ML Track Implementation

**Submission Track:** `aiml`  
**Video Walkthrough Link:** [Unlisted Video Walkthrough Link Placeholder](https://youtu.be/placeholder-vikmo-dealerdesk-aiml)

---

## 1. Project Purpose & Scope

VIKMO DealerDesk is an AI-native distributor operations platform designed for automotive and motorcycle parts supply chains across India. This repository contains the complete implementation for the **AI / ML Track**, adhering strictly to `API_CONTRACT.md`, the assignment brief, and the automated grading specification.

### Implemented Scope
- **Part A1 — Backend Core**:
  - `GET /api/health/`: System health probe.
  - Products catalog API (`GET/POST /api/products/`, `GET/PATCH /api/products/{sku}/`) with full-text search, category, and vehicle fitment filtering.
  - Inventory API (`PUT /api/inventory/{sku}/`) with audit logging.
  - Dealers management API (`GET/POST /api/dealers/`, `GET /api/dealers/{id}/`).
  - Idempotent seed data pipeline (`python manage.py seed_data`) loading exactly 600 products and 25 dealers.
- **Part A2 — Orders (full order engine)**:
  - Draft order creation (`POST /api/orders/`), retrieval (`GET /api/orders/{order_number}/`), and listing (`GET /api/orders/`).
  - Item replacement while DRAFT (`PUT /api/orders/{order_number}/`), returning `409 ORDER_LOCKED` once confirmed.
  - Lifecycle transitions (`POST /api/orders/{order_number}/confirm|dispatch|deliver|cancel/`) enforcing the §7.2 state machine, with `409 INVALID_TRANSITION` otherwise.
  - Atomic daily sequence numbering (`ORD-YYYYMMDD-NNNN`).
  - Accurate 18% GST rounding per line using `ROUND_HALF_UP` to the exact paisa.
  - All-or-nothing stock deduction on confirm under `SELECT ... FOR UPDATE` row locks (`409 INSUFFICIENT_STOCK` listing every short line), exactly-once restoration on cancel, and price locking at confirm.
  - `Idempotency-Key` support on order creation (`409 IDEMPOTENCY_CONFLICT` when reused with a different body).
- **Part B — Supplier Sync**:
  - `POST /api/sync/supplier/` pulls the whole paginated supplier feed (`next_cursor` until `null`), retries `429` per `Retry-After` and `5xx`/connection errors with capped backoff (max 5 attempts per request), and rejects invalid rows (missing `sku`, non-positive or >2-decimal `price`).
  - Deduplicates SKU collisions on latest `updated_at`, creates new catalogue products with stock 0, updates only the six mapped fields, and never touches our own stock.
  - All-or-nothing: a page that cannot be fetched after 5 attempts changes nothing and returns `502 SUPPLIER_UNAVAILABLE`.
- **Part C — Dealer Assistant (GenAI)**:
  - Conversational AI endpoint (`POST /api/assistant/chat/`) powered by Groq LLM tool calling.
  - Fully grounded tools: `search_parts`, `check_stock`, `create_draft_order`.
  - Database-verified citations (`cited_skus`) and executed side-effect actions (`actions`).
  - Persistent conversation memory isolated per `session_id`.
  - Prompt injection defense against untrusted third-party supplier catalogue descriptions.
- **Part D — Demand Forecasting & Reorder Suggestions (ML)**:
  - Committed 4-week demand forecast (`ml/forecast.csv`, 160 rows across 40 SKUs).
  - Reproducible training pipeline (`ml/train_forecast.py`) beating historical baselines by 21.4% lower MAE.
  - Forecast endpoint (`GET /api/forecast/{sku}/`).
  - Reorder suggestions endpoint (`GET /api/reorder-suggestions/`) implementing the exact sample standard deviation ($n-1$) safety stock formula with live inventory querying.

---

## 2. Architecture & Request Flow

```
[Dealer / Client] 
        │
        ├──> [GET /api/health/] ──> Status Check
        │
        ├──> [Products & Dealers] ──> DRF Views ──> B-Tree Indexed PostgreSQL
        │
        ├──> [POST /api/orders/] ──> create_order_idempotent() → create_draft_order_service()
        │                                 │
        │                                 ├──> Validates Dealer, SKUs, Qty
        │                                 ├──> Locks DailyOrderSequence atomically
        │                                 ├──> Idempotency-Key unique index replay
        │                                 └──> Calculates Line GST (ROUND_HALF_UP)
        │
        ├──> [POST /api/orders/{n}/confirm/] ──> confirm_order()
        │                                 ├──> SELECT ... FOR UPDATE on order + products
        │                                 ├──> Locks unit_price at catalogue price
        │                                 └──> All-or-nothing stock deduction
        │
        ├──> [dispatch / deliver / cancel] ──> §7.2 state machine (cancel restores stock once)
        │
        ├──> [POST /api/sync/supplier/] ──> run_supplier_sync()
        │                                 ├──> Cursor pages until next_cursor is null
        │                                 ├──> Retry-After / backoff, ≤ 5 attempts per request
        │                                 └──> Single transaction: create / update / unchanged
        │
        ├──> [POST /api/assistant/chat/] ──> process_chat_message()
        │                                         │
        │                                         ├──> Loads Session Memory
        │                                         ├──> Groq LLM (openai/gpt-oss-120b)
        │                                         │       │
        │                                         │       ├──> search_parts()
        │                                         │       ├──> check_stock()
        │                                         │       └──> create_draft_order()
        │                                         └──> Validates Citations in DB
        │
        └──> [Forecasting & Reorder]
                 ├──> [GET /api/forecast/{sku}/] ──> Reads committed ml/forecast.csv
                 └──> [GET /api/reorder-suggestions/] ──> Live Stock + Safety Stock Formula
```

---

## 3. Environment & Prerequisites

- **Python**: 3.11 or newer (tested on Python 3.14).
- **Database**: PostgreSQL 14+ (Local or Neon / Supabase).
- **LLM Provider**: Groq (API key in `GROQ_API_KEY`).
  - Model: `openai/gpt-oss-120b` (or `openai/gpt-oss-20b`, configurable via `GROQ_MODEL`).

### Environment Variables
Copy `.env.example` to `.env`:
```bash
DATABASE_URL=postgres://postgres:postgres@localhost:5432/dealerdesk
GROQ_API_KEY=your-groq-api-key
GROQ_MODEL=openai/gpt-oss-120b
DJANGO_SECRET_KEY=change-me
DJANGO_DEBUG=0

# Only needed for the supplier sync (§8)
SUPPLIER_API_URL=http://127.0.0.1:8099
SUPPLIER_API_KEY=dev-supplier-key
```

---

## 4. Setup and Run Instructions

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Apply Migrations
```bash
python manage.py migrate --noinput
```

### 3. Seed Database
```bash
python manage.py seed_data
python manage.py seed_data  # verify idempotency: runs twice with no duplicates
```

### 4. Start Server
```bash
python manage.py runserver 0.0.0.0:8000 --noreload
```

---

## 5. Automated Verification

### Running the Official Smoke Test
```bash
python smoke_test.py --base-url http://localhost:8000 --track aiml
```
**Result:** 10/10 checks pass:
- Core: Health, Products (600 seeded, pagination, fields), Detail & 404, Dealers (25 seeded & create), Inventory PUT, Orders DRAFT create, Validation error shape.
- Dealer Assistant: Response shape, tool execution, citations, actions.
- Forecasting: `ml/forecast.csv` (exact 160 rows, 4 weeks), Forecast API, Reorder suggestions API (40 SKUs).

With the bonus sections enabled (`--all`, see below) the run is **12/12**, adding the order engine and supplier sync checks.

### Running the Optional Bonus Sections
`--track aiml` does not exercise the order engine or supplier sync; use `--all` (it needs a freshly seeded database):
```bash
# 1. In one terminal, start the supplier mock and copy the URL/key it prints into .env
python supplier_mock/mock_supplier.py

# 2. In another terminal, check every section of the contract
python smoke_test.py --base-url http://localhost:8000 --all
```
Against the bundle mock the sync reports `created=12 updated=26 unchanged=31 rejected=3 pages=4 requests=6`: `created + updated + unchanged = 69`, which is the number of unique valid SKUs in that feed (§8.3.8), and re-running it reports `created=0 updated=0 unchanged=69`. Restarting the mock with `--outage` returns `502 SUPPLIER_UNAVAILABLE` with the catalogue hash unchanged, confirming §8.3.3.

### Running Unit Test Suite
```bash
python manage.py test
```
**Result:** 38/38 unit tests pass across all apps (`core`, `orders`, `forecasting`, `assistant`, `supplier`).
`orders.tests.ConfirmConcurrencyTests` fires 15 simultaneous confirms against 10 units of stock and asserts exactly 5 succeed with stock landing on 0 — the §7.3 no-oversell guarantee under the threaded dev server.

---

## 6. Machine Learning Demand Forecasting

### Pipeline & Method
- Script: `ml/train_forecast.py`
- Algorithm: `HistGradientBoostingRegressor` (scikit-learn).
- Engineered Features:
  - Autoregressive lags: `lag_1`, `lag_2`, `lag_3`, `lag_4`.
  - Rolling statistics: 4-week & 8-week rolling means, 4-week standard deviation.
  - Promotional lift: `promo_flag` from history and future `promo_calendar.csv`.
  - Moving festival seasonality: `weeks_to_diwali` based on `festival_calendar.csv`.
  - Categorical SKU identification.

### Validation Evidence (Temporal Split: 2026-08-31 to 2026-09-21)
| Model / Baseline | MAE | WAPE | Notes |
|---|---|---|---|
| Baseline 1: Last Value | 11.72 | 34.41% | Naive lag-1 |
| Baseline 2: Recent 4-week Avg | 8.76 | 25.71% | Moving average |
| Baseline 3: Same Week Last Year | 12.27 | 36.01% | 52-week lag |
| **HistGradientBoostingRegressor** | **6.88** | **20.20%** | **21.4% lower MAE than best baseline** |

### Regenerating Forecast CSV
```bash
python ml/train_forecast.py
```
This generates `ml/forecast.csv` (160 rows, 40 SKUs across weeks `2026-09-28`, `2026-10-05`, `2026-10-12`, `2026-10-19`).

---

## 7. Video Walkthrough Plan (5-Minute Outline)

- **Minute 0:00 – 0:45**: Introduction, architecture overview, and verified run contract against fresh PostgreSQL.
- **Minute 0:45 – 2:15**: Assistant tool-calling loop walkthrough (`assistant/service.py:43–214`):
  - Request ingestion, session memory loading, and system prompt defense.
  - Multi-turn tool execution (`search_parts`, `check_stock`, `create_draft_order`).
  - DB-level citation extraction and action logging.
- **Minute 2:15 – 3:30**: Shared order creation & financial calculations (`orders/services.py:131–300`):
  - Atomic daily sequence generation (`ORD-YYYYMMDD-NNNN`).
  - 18% GST line-by-line `ROUND_HALF_UP` precision.
- **Minute 3:30 – 4:30**: Demand forecasting & reorder engine (`ml/train_forecast.py`, `forecasting/services.py`):
  - Explaining the 21.4% improvement over historical baselines.
  - Sample standard deviation ($n-1$) safety stock calculations with live inventory.
- **Minute 4:30 – 5:00**: Live execution of `smoke_test.py --track aiml` (10/10 PASS) and closing summary.
