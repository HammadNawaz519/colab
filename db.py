"""Colab database helper with full embedded SQLite schema."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

COLAB_DIR = Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd()
DB_PATH = COLAB_DIR / "shopy_colab.db"
SHOPY_SQL_PATH = COLAB_DIR / "shopy.sql"

BASE_SCHEMA_SQL = """PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL,
    phone_number  TEXT,
    email         TEXT NOT NULL UNIQUE,
    password      TEXT NOT NULL,
    verified      INTEGER NOT NULL DEFAULT 0,
    role          TEXT NOT NULL DEFAULT 'customer',
    profile_pic   TEXT,
    bio           TEXT,
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS categories (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL UNIQUE,
    slug          TEXT UNIQUE,
    description   TEXT,
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS products (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    retailer_id    INTEGER NOT NULL,
    category_id    INTEGER,
    name           TEXT NOT NULL,
    description    TEXT,
    price          REAL NOT NULL,
    original_price REAL,
    stock          INTEGER NOT NULL DEFAULT 0,
    image_url      TEXT,
    sku            TEXT UNIQUE,
    weight_grams   INTEGER,
    is_active      INTEGER NOT NULL DEFAULT 1,
    created_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (retailer_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS orders (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id      INTEGER NOT NULL,
    total_amount     REAL NOT NULL DEFAULT 0,
    status           TEXT NOT NULL DEFAULT 'pending',
    shipping_name    TEXT,
    shipping_address TEXT,
    shipping_phone   TEXT,
    notes            TEXT,
    discount_code_id INTEGER,
    discount_amount  REAL NOT NULL DEFAULT 0,
    payment_method   TEXT NOT NULL DEFAULT 'cod',
    payment_status   TEXT NOT NULL DEFAULT 'pending',
    payment_ref      TEXT,
    created_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (customer_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (discount_code_id) REFERENCES discount_codes(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS order_items (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id          INTEGER NOT NULL,
    product_id        INTEGER,
    retailer_id       INTEGER,
    product_name      TEXT,
    quantity          INTEGER NOT NULL,
    unit_price        REAL NOT NULL,
    discount_per_unit REAL NOT NULL DEFAULT 0,
    FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE SET NULL,
    FOREIGN KEY (retailer_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS reviews (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id           INTEGER NOT NULL,
    user_id              INTEGER NOT NULL,
    rating               INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    title                TEXT,
    comment              TEXT,
    is_verified_purchase INTEGER NOT NULL DEFAULT 0,
    created_at           TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (product_id, user_id),
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS discount_codes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    code            TEXT NOT NULL UNIQUE,
    campaign_name   TEXT,
    discount_type   TEXT NOT NULL DEFAULT 'percent',
    discount_value  REAL NOT NULL,
    min_order_value REAL NOT NULL DEFAULT 0,
    max_uses        INTEGER,
    uses_count      INTEGER NOT NULL DEFAULT 0,
    expires_at      TEXT,
    is_active       INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS addresses (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL,
    label        TEXT NOT NULL DEFAULT 'Home',
    full_name    TEXT,
    phone        TEXT,
    address_line TEXT NOT NULL,
    city         TEXT,
    province     TEXT,
    postal_code  TEXT,
    country      TEXT NOT NULL DEFAULT 'Pakistan',
    is_default   INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS notifications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    message    TEXT NOT NULL,
    type       TEXT NOT NULL DEFAULT 'info',
    channel    TEXT NOT NULL DEFAULT 'in_app',
    action_url TEXT,
    is_read    INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ai_chat_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    role       TEXT NOT NULL DEFAULT 'customer',
    sender     TEXT NOT NULL,
    message    TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS product_images (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id          INTEGER NOT NULL,
    image_url           TEXT NOT NULL,
    alt_text            TEXT,
    sort_order          INTEGER NOT NULL DEFAULT 1,
    is_primary          INTEGER NOT NULL DEFAULT 0,
    uploaded_by_user_id INTEGER,
    created_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
    FOREIGN KEY (uploaded_by_user_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS cart (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    product_id  INTEGER NOT NULL,
    quantity    INTEGER NOT NULL DEFAULT 1,
    added_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, product_id),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS wishlists (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id              INTEGER NOT NULL,
    product_id           INTEGER NOT NULL,
    priority             INTEGER NOT NULL DEFAULT 3,
    target_price         REAL,
    notify_on_price_drop INTEGER NOT NULL DEFAULT 0,
    note                 TEXT,
    added_at             TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (priority BETWEEN 1 AND 5),
    UNIQUE (user_id, product_id),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS payments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id        INTEGER NOT NULL,
    payment_method  TEXT NOT NULL,
    provider        TEXT,
    amount_paid     REAL NOT NULL,
    payment_status  TEXT NOT NULL DEFAULT 'pending',
    transaction_ref TEXT,
    paid_at         TEXT,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (order_id, transaction_ref),
    FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS shipments (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id          INTEGER NOT NULL,
    courier_name      TEXT NOT NULL,
    tracking_number   TEXT UNIQUE,
    shipment_status   TEXT NOT NULL DEFAULT 'pending',
    shipped_at        TEXT,
    expected_delivery TEXT,
    delivered_at      TEXT,
    shipping_cost     REAL NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE
);
"""


def _ensure_colab_path() -> None:
    colab_path = str(COLAB_DIR)
    if colab_path not in sys.path:
        sys.path.insert(0, colab_path)


def _resolve_main(main_module: Any | None = None) -> Any:
    if main_module is not None:
        return main_module

    _ensure_colab_path()
    import main as local_main

    return local_main


def ensure_local_sql_file() -> None:
    if SHOPY_SQL_PATH.exists():
        return
    SHOPY_SQL_PATH.write_text(BASE_SCHEMA_SQL.strip() + "\n", encoding="utf-8")


def apply_db_overrides_to_main(main_module: Any | None = None) -> Any:
    """Attach local Colab DB paths + schema to main.py module."""
    main = _resolve_main(main_module)
    main.ROOT_DIR = COLAB_DIR
    main.DB_PATH = DB_PATH
    main.SHOPY_SQL_PATH = SHOPY_SQL_PATH
    main.BASE_SCHEMA_SQL = BASE_SCHEMA_SQL
    return main


def bootstrap_database_only(main_module: Any | None = None) -> None:
    """Run bootstrap once (safe to call repeatedly)."""
    main = apply_db_overrides_to_main(main_module)
    ensure_local_sql_file()
    main.bootstrap_database(DB_PATH, SHOPY_SQL_PATH)


def colab_db_cell() -> str:
    """Notebook helper snippet."""
    return "import sys; sys.path.insert(0, r'./colab'); from db import bootstrap_database_only; bootstrap_database_only()"
