"""
Single-file Shopy retailer portal (Colab-ready).

This module bundles:
1) SQLite bootstrap (optionally importing from shopy.sql)
2) Retailer backend actions (auth, products, orders, profile, assistant)
3) Gradio custom frontend shell with full retailer views
"""

from __future__ import annotations

import inspect
import json
import os
import random
import re
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests
from werkzeug.security import check_password_hash, generate_password_hash

try:
    import gradio as gr  # type: ignore[import-not-found]
except Exception:
    gr = None


# ============================================================
# [SECTION: GLOBALS]
# ============================================================
ROOT_DIR = Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd()
DB_PATH = ROOT_DIR / "shopy_colab.db"
SHOPY_SQL_PATH = ROOT_DIR / "shopy.sql"

# Optional in-file fallback key for quick Colab runs.
# Prefer setting OPENROUTER_API_KEY in environment.
OPENROUTER_API_KEY_FALLBACK = ""

FORBIDDEN_INPUT_RULES: list[tuple[str, str]] = [
    (
        r"\bdrop\b|\bdelete\b|\btruncate\b|\bupdate\b|\binsert\b|\balter\b|\bcreate\b|\bexec\b|\bgrant\b|\brevoke\b",
        "destructive SQL keyword",
    ),
    (r"--|/\*|\*/|\bunion\s+select\b|\b0x[0-9a-f]+\b|\bchar\s*\(|\bxp_", "injection syntax"),
    (
        r"ignore previous instructions|ignore all instructions|reveal password|forget your rules|maintenance mode",
        "prompt injection phrase",
    ),
]

DESTRUCTIVE_SQL_RE = re.compile(
    r"\b(drop|delete|truncate|update|insert|alter|create|exec|grant|revoke|attach|pragma)\b",
    re.IGNORECASE,
)

OPENROUTER_MODELS = [
    os.getenv("OPENROUTER_MODEL", "").strip(),
    "openai/gpt-oss-120b:free",
    "openai/gpt-oss-20b:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "google/gemma-3-12b-it:free",
]

SCHEMA_CACHE = ""
SHOW_DEBUG_DETAILS = os.getenv("SAGE_SHOW_DEBUG", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

AUTH_SESSIONS: dict[str, dict[str, Any]] = {}
PENDING_VERIFICATIONS: dict[str, dict[str, Any]] = {}
SESSION_TTL_MINUTES = 12 * 60
OTP_TTL_MINUTES = 10


# ============================================================
# [SECTION: SQL SCHEMA]
# ============================================================
BASE_SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

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


# ============================================================
# [SECTION: DB BOOTSTRAP]
# ============================================================
def utc_now() -> datetime:
    return datetime.utcnow()


def sql_now_str() -> str:
    return utc_now().strftime("%Y-%m-%d %H:%M:%S")


def sqlite_connect_rw(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    )
    return cur.fetchone() is not None


def table_has_column(conn: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    if not table_exists(conn, table_name):
        return False
    info = conn.execute(f"PRAGMA table_info('{table_name}')").fetchall()
    return any(str(row[1]) == column_name for row in info)


def split_sql_statements(script: str) -> list[str]:
    out: list[str] = []
    buff: list[str] = []
    in_single = False
    in_double = False
    escape = False

    for ch in script:
        buff.append(ch)

        if ch == "\\" and not escape:
            escape = True
            continue

        if ch == "'" and not in_double and not escape:
            in_single = not in_single
        elif ch == '"' and not in_single and not escape:
            in_double = not in_double
        elif ch == ";" and not in_single and not in_double:
            stmt = "".join(buff).strip()
            if stmt:
                out.append(stmt)
            buff = []

        escape = False

    tail = "".join(buff).strip()
    if tail:
        out.append(tail)
    return out


def convert_mysql_statement_to_sqlite(statement: str) -> str:
    s = statement.strip()
    if not s:
        return ""

    upper = s.upper()
    if upper.startswith("CREATE DATABASE"):
        return ""
    if upper.startswith("DROP DATABASE"):
        return ""
    if upper.startswith("USE "):
        return ""
    if upper.startswith("DELIMITER"):
        return ""
    if upper.startswith("SET "):
        return ""

    s = s.replace("`", "")
    s = s.replace("INSERT IGNORE", "INSERT OR IGNORE")
    s = re.sub(r"ENGINE\s*=\s*\w+", "", s, flags=re.IGNORECASE)
    s = re.sub(r"DEFAULT\s+CHARSET\s*=\s*\w+", "", s, flags=re.IGNORECASE)
    s = re.sub(r"CHARSET\s*=\s*\w+", "", s, flags=re.IGNORECASE)
    s = re.sub(r"COLLATE\s*=\s*\w+", "", s, flags=re.IGNORECASE)
    s = re.sub(r"AUTO_INCREMENT\s*=\s*\d+", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\bUNSIGNED\b", "", s, flags=re.IGNORECASE)

    s = re.sub(r"\bTINYINT\(1\)\b", "INTEGER", s, flags=re.IGNORECASE)
    s = re.sub(r"\bINT\(\d+\)\b", "INTEGER", s, flags=re.IGNORECASE)
    s = re.sub(r"\bBIGINT\(\d+\)\b", "INTEGER", s, flags=re.IGNORECASE)
    s = re.sub(r"\bINT\b", "INTEGER", s, flags=re.IGNORECASE)
    s = re.sub(r"\bDOUBLE\b", "REAL", s, flags=re.IGNORECASE)
    s = re.sub(r"\bFLOAT\b", "REAL", s, flags=re.IGNORECASE)
    s = re.sub(r"\bDECIMAL\s*\(\s*\d+\s*,\s*\d+\s*\)", "REAL", s, flags=re.IGNORECASE)
    s = re.sub(r"\bVARCHAR\s*\(\s*\d+\s*\)", "TEXT", s, flags=re.IGNORECASE)
    s = re.sub(r"\bLONGTEXT\b|\bMEDIUMTEXT\b|\bTEXT\b", "TEXT", s, flags=re.IGNORECASE)
    s = re.sub(r"\bDATETIME\b|\bTIMESTAMP\b", "TEXT", s, flags=re.IGNORECASE)

    s = re.sub(
        r"DEFAULT\s+CURRENT_TIMESTAMP\s+ON\s+UPDATE\s+CURRENT_TIMESTAMP",
        "DEFAULT CURRENT_TIMESTAMP",
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(r"ON\s+UPDATE\s+CURRENT_TIMESTAMP", "", s, flags=re.IGNORECASE)
    s = re.sub(
        r"INTEGER\s+AUTO_INCREMENT\s+PRIMARY\s+KEY",
        "INTEGER PRIMARY KEY AUTOINCREMENT",
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(
        r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT",
        "INTEGER PRIMARY KEY AUTOINCREMENT",
        s,
        flags=re.IGNORECASE,
    )

    if re.match(r"(?is)^\s*CREATE\s+TABLE", s):
        lines: list[str] = []
        for line in s.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if re.match(r"(?i)^KEY\s+", stripped):
                continue
            if re.match(r"(?i)^INDEX\s+", stripped):
                continue

            m_unique = re.match(r"(?i)^UNIQUE\s+KEY\s+\w+\s*(\(.+\))\s*,?$", stripped)
            if m_unique:
                indent = line[: len(line) - len(line.lstrip())]
                trailing = "," if stripped.endswith(",") else ""
                line = f"{indent}UNIQUE {m_unique.group(1)}{trailing}"

            lines.append(line)

        s = "\n".join(lines)
        s = re.sub(r",\s*\)", "\n)", s, flags=re.MULTILINE)

    return s.strip()


def convert_mysql_to_sqlite_script(mysql_sql: str) -> str:
    no_block_comments = re.sub(r"/\*[\s\S]*?\*/", "", mysql_sql)
    clean_lines = []
    for line in no_block_comments.splitlines():
        if line.strip().startswith("--"):
            continue
        clean_lines.append(line)

    cleaned = "\n".join(clean_lines)
    converted: list[str] = []
    for stmt in split_sql_statements(cleaned):
        out = convert_mysql_statement_to_sqlite(stmt)
        if out:
            converted.append(out.rstrip(";") + ";")
    return "\n\n".join(converted)


def execute_sqlite_script_safe(conn: sqlite3.Connection, sqlite_script: str) -> None:
    for stmt in split_sql_statements(sqlite_script):
        txt = stmt.strip().rstrip(";")
        if not txt:
            continue
        try:
            conn.execute(txt)
        except sqlite3.Error:
            # Best-effort migration from MySQL script.
            continue


def ensure_base_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(BASE_SCHEMA_SQL)


def seed_database(conn: sqlite3.Connection) -> None:
    now = sql_now_str()

    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM categories")
    category_count = int(cur.fetchone()[0] or 0)
    if category_count == 0:
        categories = [
            ("Electronics", "electronics", "Smart devices and accessories"),
            ("Fashion", "fashion", "Clothing and style essentials"),
            ("Home & Garden", "home-garden", "Home appliances and decor"),
            ("Sports", "sports", "Fitness and sports gear"),
            ("Books", "books", "Books and educational content"),
            ("Beauty", "beauty", "Skincare and beauty products"),
            ("Toys", "toys", "Toys and games"),
            ("Food", "food", "Edibles and pantry items"),
            ("Health", "health", "Health and wellness"),
            ("Automobiles", "automobiles", "Automotive accessories"),
        ]
        cur.executemany(
            """
            INSERT OR IGNORE INTO categories (name, slug, description, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            [(n, s, d, now, now) for (n, s, d) in categories],
        )

    cur.execute("SELECT COUNT(*) FROM users")
    user_count = int(cur.fetchone()[0] or 0)
    if user_count == 0:
        users = [
            (
                "retailer_alpha",
                "03001112233",
                "alpha@shopy.pk",
                generate_password_hash("retailer123"),
                1,
                "retailer",
                "Retailer Alpha account",
            ),
            (
                "retailer_beta",
                "03002223344",
                "beta@shopy.pk",
                generate_password_hash("retailer123"),
                1,
                "retailer",
                "Retailer Beta account",
            ),
            (
                "ali",
                "03010000001",
                "ali@shopy.pk",
                generate_password_hash("customer123"),
                1,
                "customer",
                "Ali customer account",
            ),
            (
                "fatima",
                "03010000002",
                "fatima@shopy.pk",
                generate_password_hash("customer123"),
                1,
                "customer",
                "Fatima customer account",
            ),
        ]
        cur.executemany(
            """
            INSERT OR IGNORE INTO users
            (username, phone_number, email, password, verified, role, bio, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (u, p, e, pw, v, r, bio, now, now)
                for (u, p, e, pw, v, r, bio) in users
            ],
        )

    cur.execute("SELECT COUNT(*) FROM products")
    product_count = int(cur.fetchone()[0] or 0)
    if product_count == 0:
        cur.execute("SELECT id FROM users WHERE role='retailer' ORDER BY id")
        retailer_ids = [int(r[0]) for r in cur.fetchall()]

        cur.execute("SELECT id, name FROM categories ORDER BY id")
        cat_rows = cur.fetchall()
        category_ids = [int(r[0]) for r in cat_rows]

        sample_products = [
            ("Noise-Cancel Headphones X1", 12999.0, 19),
            ("Wireless Earbuds Lite", 5999.0, 24),
            ("Mechanical Keyboard Pro", 8499.0, 11),
            ("4K Smart Monitor 27", 48999.0, 8),
            ("Fitness Watch Active", 13999.0, 15),
            ("Vitamin C Face Serum", 1899.0, 29),
            ("Running Shoes Sprint", 6799.0, 18),
            ("Yoga Mat Premium", 2499.0, 22),
            ("Air Fryer Compact", 15999.0, 9),
            ("Portable SSD 1TB", 12499.0, 13),
            ("STEM Robot Kit", 7499.0, 17),
            ("Car Dash Cam HD", 9999.0, 10),
        ]

        prod_rows: list[tuple[Any, ...]] = []
        for idx, (name, price, stock) in enumerate(sample_products):
            retailer_id = retailer_ids[idx % max(1, len(retailer_ids))] if retailer_ids else 1
            cat_id = category_ids[idx % max(1, len(category_ids))] if category_ids else None
            prod_rows.append(
                (
                    retailer_id,
                    cat_id,
                    name,
                    f"{name} suitable for daily use.",
                    float(price),
                    float(price) + random.choice([500.0, 900.0, 1200.0]),
                    int(stock),
                    "https://example.com/product.jpg",
                    f"SKU-{idx+1:03d}",
                    random.choice([200, 350, 500, 900]),
                    1,
                    now,
                    now,
                )
            )

        cur.executemany(
            """
            INSERT INTO products
            (retailer_id, category_id, name, description, price, original_price, stock,
             image_url, sku, weight_grams, is_active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            prod_rows,
        )

    cur.execute("SELECT COUNT(*) FROM orders")
    order_count = int(cur.fetchone()[0] or 0)
    if order_count == 0:
        cur.execute("SELECT id, username, phone_number FROM users WHERE role='customer' ORDER BY id")
        customers = [dict(r) for r in cur.fetchall()]

        cur.execute("SELECT id, name, price, retailer_id FROM products ORDER BY id")
        products = [dict(r) for r in cur.fetchall()]

        statuses = ["pending", "processing", "shipped", "delivered", "cancelled"]
        for i in range(12):
            customer = customers[i % max(1, len(customers))] if customers else {
                "id": 1,
                "username": "Customer",
                "phone_number": "03000000000",
            }
            created_at = (utc_now() - timedelta(days=(12 - i))).strftime("%Y-%m-%d %H:%M:%S")
            status = statuses[i % len(statuses)]

            cur.execute(
                """
                INSERT INTO orders
                (customer_id, total_amount, status, shipping_name, shipping_address,
                 shipping_phone, notes, payment_method, payment_status, payment_ref,
                 created_at, updated_at)
                VALUES (?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(customer["id"]),
                    status,
                    str(customer["username"]),
                    f"Street {i+1}, Lahore",
                    str(customer.get("phone_number") or "03000000000"),
                    "Auto-generated seed order",
                    random.choice(["cod", "card"]),
                    "paid" if status in {"processing", "shipped", "delivered"} else "pending",
                    f"PAYREF{2000+i}",
                    created_at,
                    created_at,
                ),
            )
            order_id = int(cur.lastrowid)

            total_amount = 0.0
            for j in range(2):
                p = products[(i + j) % max(1, len(products))]
                qty = 1 + ((i + j) % 3)
                unit_price = float(p["price"])
                total_amount += qty * unit_price

                cur.execute(
                    """
                    INSERT INTO order_items
                    (order_id, product_id, retailer_id, product_name, quantity, unit_price, discount_per_unit)
                    VALUES (?, ?, ?, ?, ?, ?, 0)
                    """,
                    (
                        order_id,
                        int(p["id"]),
                        int(p["retailer_id"]),
                        str(p["name"]),
                        qty,
                        unit_price,
                    ),
                )

            cur.execute(
                "UPDATE orders SET total_amount=?, updated_at=? WHERE id=?",
                (round(total_amount, 2), created_at, order_id),
            )

    cur.close()


def bootstrap_database(db_path: Path = DB_PATH, schema_path: Path = SHOPY_SQL_PATH) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite_connect_rw(db_path)

    try:
        needs_bootstrap = not table_exists(conn, "users")
        if needs_bootstrap and schema_path.exists():
            raw = schema_path.read_text(encoding="utf-8", errors="ignore")
            sqlite_script = convert_mysql_to_sqlite_script(raw)
            if sqlite_script.strip():
                execute_sqlite_script_safe(conn, sqlite_script)

        ensure_base_schema(conn)
        seed_database(conn)
        conn.commit()
    finally:
        conn.close()


# ============================================================
# [SECTION: AI PIPELINE]
# ============================================================
def get_schema_overview(db_path: Path = DB_PATH) -> str:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    cur = conn.cursor()
    cur.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type='table' AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    )
    tables = [row["name"] for row in cur.fetchall()]

    lines: list[str] = ["Database: shopy_colab (SQLite)"]
    for table in tables:
        lines.append(f"\nTable: {table}")
        cols = conn.execute(f"PRAGMA table_info('{table}')").fetchall()
        for col in cols:
            col_name = col[1]
            col_type = col[2]
            not_null = "NOT NULL" if col[3] else "NULLABLE"
            pk = "PK" if col[5] else ""
            lines.append(f"- {col_name} {col_type} {not_null} {pk}".strip())

        fk_rows = conn.execute(f"PRAGMA foreign_key_list('{table}')").fetchall()
        for fk in fk_rows:
            lines.append(f"  FK: {fk[3]} -> {fk[2]}.{fk[4]}")

    conn.close()
    return "\n".join(lines)


def user_input_blocked(user_text: str) -> tuple[bool, str]:
    txt = (user_text or "").strip().lower()
    for pattern, reason in FORBIDDEN_INPUT_RULES:
        if re.search(pattern, txt, flags=re.IGNORECASE):
            return True, reason
    return False, ""


def openrouter_chat(
    messages: list[dict[str, str]],
    temperature: float = 0.0,
    max_tokens: int = 700,
) -> tuple[str, str, str]:
    api_key = (
        os.getenv("OPENROUTER_API_KEY", "").strip()
        or OPENROUTER_API_KEY_FALLBACK.strip()
    )
    if not api_key:
        return "", "", "OPENROUTER_API_KEY is not set"

    models: list[str] = []
    seen: set[str] = set()
    for m in OPENROUTER_MODELS:
        candidate = (m or "").strip()
        if candidate and candidate not in seen:
            seen.add(candidate)
            models.append(candidate)

    last_error = "Unknown OpenRouter error"

    for model in models:
        try:
            resp = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://colab.research.google.com",
                    "X-Title": "Shopy Conversational DB Assistant",
                },
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
                timeout=40,
            )

            if resp.status_code in (404, 429):
                last_error = f"{model} returned HTTP {resp.status_code}"
                continue
            if resp.status_code == 402:
                return "", model, "OpenRouter credits required"

            resp.raise_for_status()
            payload = resp.json()

            choices = payload.get("choices") or []
            if not choices:
                last_error = f"{model} returned no choices"
                continue

            content = choices[0].get("message", {}).get("content", "").strip()
            content = re.sub(r"<think>[\s\S]*?</think>", "", content, flags=re.IGNORECASE).strip()
            if content:
                return content, model, ""

            last_error = f"{model} returned empty content"
        except Exception as exc:
            last_error = f"{model} failed: {exc}"

    return "", "", last_error


def extract_sql_from_text(raw_text: str) -> str:
    text = (raw_text or "").strip()

    fenced = re.findall(r"```(?:sql)?\s*([\s\S]*?)```", text, flags=re.IGNORECASE)
    if fenced:
        text = fenced[0].strip()

    match = re.search(r"(?is)\bselect\b[\s\S]*", text)
    if match:
        text = match.group(0).strip()

    if ";" in text:
        text = text.split(";", 1)[0].strip() + ";"
    else:
        text = text.rstrip() + ";"

    return text


def fallback_sql(question: str) -> str:
    q = (question or "").lower()

    if "top" in q and ("sell" in q or "popular" in q or "demand" in q):
        return """
SELECT p.name,
       SUM(oi.quantity) AS units_sold,
       ROUND(SUM(oi.quantity * oi.unit_price), 2) AS revenue
FROM order_items oi
JOIN products p ON p.id = oi.product_id
GROUP BY p.id, p.name
ORDER BY units_sold DESC
LIMIT 10;
""".strip()

    if "low stock" in q or "out of stock" in q or "stock" in q:
        return """
SELECT name, stock, price
FROM products
WHERE is_active = 1
ORDER BY stock ASC, price DESC
LIMIT 10;
""".strip()

    if "revenue" in q or "sales" in q:
        return """
SELECT strftime('%Y-%m', o.created_at) AS month,
       ROUND(SUM(oi.quantity * oi.unit_price), 2) AS gross_revenue,
       COUNT(DISTINCT o.id) AS order_count
FROM orders o
JOIN order_items oi ON oi.order_id = o.id
WHERE o.status != 'cancelled'
GROUP BY strftime('%Y-%m', o.created_at)
ORDER BY month DESC
LIMIT 12;
""".strip()

    if "order" in q and "status" in q:
        return """
SELECT status, COUNT(*) AS total_orders, ROUND(SUM(total_amount), 2) AS total_amount
FROM orders
GROUP BY status
ORDER BY total_orders DESC;
""".strip()

    if "rating" in q or "review" in q:
        return """
SELECT p.name,
       ROUND(AVG(r.rating), 2) AS avg_rating,
       COUNT(r.id) AS review_count
FROM products p
LEFT JOIN reviews r ON r.product_id = p.id
GROUP BY p.id, p.name
ORDER BY avg_rating DESC, review_count DESC
LIMIT 10;
""".strip()

    return """
SELECT p.name, c.name AS category, p.price, p.stock
FROM products p
LEFT JOIN categories c ON c.id = p.category_id
WHERE p.is_active = 1
ORDER BY p.created_at DESC
LIMIT 10;
""".strip()


def generate_sql(question: str, schema_text: str) -> tuple[str, str]:
    system_prompt = (
        "You are a secure, read-only SQLite SQL generator. "
        "Return exactly one SQL query and nothing else. "
        "Never write UPDATE/DELETE/INSERT/ALTER/CREATE/DROP/GRANT/REVOKE/EXEC. "
        "Use only tables and columns present in the provided schema. "
        "If the user asks for unsafe actions, still return a harmless SELECT query that explains impossibility."
    )

    user_prompt = (
        "Database schema:\n"
        f"{schema_text}\n\n"
        "Task: Convert the following natural-language question into one SQLite SELECT query.\n"
        f"Question: {question}\n\n"
        "Output format: SQL only."
    )

    llm_text, model, err = openrouter_chat(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.0,
        max_tokens=300,
    )

    if llm_text:
        return extract_sql_from_text(llm_text), f"LLM model: {model}"

    return fallback_sql(question), f"Heuristic fallback ({err})"


def validate_sql(sql_text: str) -> tuple[bool, str]:
    sql = (sql_text or "").strip()
    if not sql:
        return False, "Empty SQL output"

    if not re.match(r"(?is)^\s*select\b", sql):
        return False, "Only SELECT is allowed"

    if DESTRUCTIVE_SQL_RE.search(sql):
        return False, "Detected destructive keyword in SQL output"

    if ";" in sql.strip()[:-1]:
        return False, "Multiple SQL statements are not allowed"

    if re.search(r"(?is)\bpragma\b|\battach\b|\bdetach\b", sql):
        return False, "Disallowed SQLite operation"

    return True, sql


def execute_query_readonly(sql_text: str, db_path: Path = DB_PATH) -> list[dict[str, Any]]:
    uri = f"file:{db_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON;")

    cur = conn.cursor()
    cur.execute(sql_text)
    rows = [dict(row) for row in cur.fetchall()]

    conn.close()
    return rows


def synthesize_answer(question: str, sql_text: str, rows: list[dict[str, Any]]) -> tuple[str, str]:
    if not rows:
        return "No matching data was found.", "Synthesis: empty-result"

    preview = json.dumps(rows[:20], ensure_ascii=True)
    prompt = (
        "You are a concise business analyst.\n"
        "Given the user question, SQL query, and query result rows, produce a clear answer in plain language.\n"
        "Mention important numbers and trends without exposing internal chain-of-thought.\n"
        "Keep it practical and actionable for a retailer.\n\n"
        f"Question: {question}\n"
        f"SQL: {sql_text}\n"
        f"Rows JSON: {preview}"
    )

    llm_text, model, err = openrouter_chat(
        [
            {"role": "system", "content": "You summarize SQL results for retail business users."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        max_tokens=420,
    )

    if llm_text:
        return llm_text.strip(), f"Synthesis LLM: {model}"

    first_row = rows[0]
    keys = ", ".join(first_row.keys())
    return (
        f"Found {len(rows)} row(s). Key fields include: {keys}.",
        f"Synthesis fallback ({err})",
    )


def rows_to_debug_text(rows: list[dict[str, Any]], max_rows: int = 25) -> str:
    if not rows:
        return "[]"
    clipped = rows[:max_rows]
    body = json.dumps(clipped, indent=2, ensure_ascii=True)
    if len(rows) > max_rows:
        body += f"\n... {len(rows) - max_rows} more row(s) omitted"
    return body


def process_question(question: str) -> tuple[str, str, str, str]:
    user_q = (question or "").strip()
    if not user_q:
        return (
            "Please type a question first.",
            "",
            "",
            "Status: waiting for input",
        )

    blocked, reason = user_input_blocked(user_q)
    if blocked:
        return (
            f"Request rejected by Layer-2 keyword blocklist ({reason}).",
            "",
            "",
            "Status: blocked before LLM",
        )

    global SCHEMA_CACHE
    if not SCHEMA_CACHE:
        SCHEMA_CACHE = get_schema_overview(DB_PATH)

    generated_sql, sql_source = generate_sql(user_q, SCHEMA_CACHE)

    ok, validated_or_error = validate_sql(generated_sql)
    if not ok:
        return (
            f"Request rejected by Layer-4 output validation: {validated_or_error}",
            generated_sql,
            "",
            f"Status: {sql_source}",
        )

    validated_sql = validated_or_error

    try:
        rows = execute_query_readonly(validated_sql, DB_PATH)
    except Exception as exc:
        safe_err = str(exc).splitlines()[0][:300]
        return (
            f"The query could not be executed safely: {safe_err}",
            validated_sql,
            "",
            f"Status: {sql_source}",
        )

    final_answer, synth_status = synthesize_answer(user_q, validated_sql, rows)
    debug_rows = rows_to_debug_text(rows)

    status = (
        f"{sql_source} | Rows: {len(rows)} | DB mode: read-only sqlite connection | {synth_status}"
    )

    return final_answer, validated_sql, debug_rows, status


def process_question_with_mode(question: str, mode: str) -> tuple[str, str, str, str]:
    answer, sql_text, db_output, status = process_question(question)
    mode_label = (mode or "Assistant").strip()
    if status:
        status = f"Mode: {mode_label} | {status}"
    else:
        status = f"Mode: {mode_label}"
    return answer, sql_text, db_output, status


def build_ui_payload(question: str, mode: str) -> str:
    answer, sql_text, db_output, status = process_question_with_mode(question, mode)
    payload = {
        "answer": answer,
        "ts": datetime.utcnow().isoformat(),
    }

    if SHOW_DEBUG_DETAILS:
        payload["query_ran"] = sql_text
        payload["db_output"] = db_output
        payload["status"] = status

    return json.dumps(payload, ensure_ascii=True)


# ============================================================
# [SECTION: AUTH + RETAILER ACTIONS]
# ============================================================
def parse_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def parse_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def password_matches(raw_password: str, stored_password: str) -> bool:
    if not stored_password:
        return False
    if raw_password == stored_password:
        return True
    try:
        return check_password_hash(stored_password, raw_password)
    except Exception:
        return False


def generate_otp_code() -> str:
    return "".join(str(random.randint(0, 9)) for _ in range(6))


def create_auth_session(user_row: dict[str, Any]) -> str:
    token = uuid.uuid4().hex
    AUTH_SESSIONS[token] = {
        "user_id": int(user_row["id"]),
        "username": str(user_row.get("username") or "Retailer"),
        "email": str(user_row.get("email") or ""),
        "role": str(user_row.get("role") or "customer"),
        "expires_at": utc_now() + timedelta(minutes=SESSION_TTL_MINUTES),
    }
    return token


def get_auth_session(token: str) -> dict[str, Any] | None:
    token = (token or "").strip()
    if not token:
        return None
    session_info = AUTH_SESSIONS.get(token)
    if not session_info:
        return None
    if session_info["expires_at"] < utc_now():
        AUTH_SESSIONS.pop(token, None)
        return None

    session_info["expires_at"] = utc_now() + timedelta(minutes=SESSION_TTL_MINUTES)
    return session_info


def require_retailer_session(token: str) -> tuple[dict[str, Any] | None, str]:
    session_info = get_auth_session(token)
    if not session_info:
        return None, "Session expired. Please login again."
    if (session_info.get("role") or "") != "retailer":
        return None, "Retailer access is required for this action."
    return session_info, ""


def action_login(data: dict[str, Any]) -> dict[str, Any]:
    email = str(data.get("email") or "").strip().lower()
    password = str(data.get("password") or "")
    requested_role = str(data.get("role") or "retailer").strip().lower() or "retailer"

    if not email or not password:
        raise ValueError("Email and password are required.")

    conn = sqlite_connect_rw()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, username, email, password, verified, role, created_at
        FROM users
        WHERE LOWER(email) = LOWER(?)
        LIMIT 1
        """,
        (email,),
    )
    user = cur.fetchone()
    cur.close()
    conn.close()

    if not user:
        raise ValueError("Invalid email or password.")

    user_role = str(user["role"] or "customer")
    if user_role != requested_role:
        raise ValueError(f"This account is registered as {user_role}. Select the correct role.")

    if int(user["verified"] or 0) != 1:
        raise ValueError("Please verify your account first.")

    if not password_matches(password, str(user["password"] or "")):
        raise ValueError("Invalid email or password.")

    user_row = dict(user)
    token = create_auth_session(user_row)

    return {
        "session_token": token,
        "user": {
            "id": int(user_row["id"]),
            "username": str(user_row.get("username") or "Retailer"),
            "email": str(user_row.get("email") or ""),
            "role": user_role,
            "created_at": str(user_row.get("created_at") or ""),
        },
    }


def action_register(data: dict[str, Any]) -> dict[str, Any]:
    username = str(data.get("username") or "").strip()
    phone = str(data.get("phone") or "").strip()
    email = str(data.get("email") or "").strip().lower()
    password = str(data.get("password") or "")
    role = str(data.get("role") or "retailer").strip().lower() or "retailer"
    if role not in {"customer", "retailer"}:
        role = "retailer"

    if not username or not email or not password:
        raise ValueError("Username, email, and password are required.")

    conn = sqlite_connect_rw()
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE LOWER(email) = LOWER(?)", (email,))
    exists = cur.fetchone()
    cur.close()
    conn.close()
    if exists:
        raise ValueError("An account with this email already exists.")

    reg_token = uuid.uuid4().hex
    otp = generate_otp_code()
    PENDING_VERIFICATIONS[reg_token] = {
        "username": username[:80],
        "phone": phone[:30],
        "email": email,
        "password_hash": generate_password_hash(password),
        "role": role,
        "otp": otp,
        "expires_at": utc_now() + timedelta(minutes=OTP_TTL_MINUTES),
    }

    return {
        "pending_token": reg_token,
        "message": "OTP generated. Verify your account to continue.",
        "otp_hint": otp,
        "expires_in_minutes": OTP_TTL_MINUTES,
    }


def action_verify_otp(data: dict[str, Any]) -> dict[str, Any]:
    pending_token = str(data.get("pending_token") or "").strip()
    otp = str(data.get("otp") or "").strip()
    if not pending_token or not otp:
        raise ValueError("Pending token and OTP are required.")

    pending = PENDING_VERIFICATIONS.get(pending_token)
    if not pending:
        raise ValueError("Registration session not found. Register again.")

    if pending["expires_at"] < utc_now():
        PENDING_VERIFICATIONS.pop(pending_token, None)
        raise ValueError("OTP expired. Please register again.")

    if otp != str(pending["otp"]):
        raise ValueError("Invalid OTP.")

    now = sql_now_str()
    conn = sqlite_connect_rw()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO users
            (username, phone_number, email, password, verified, role, profile_pic, bio, created_at, updated_at)
            VALUES (?, ?, ?, ?, 1, ?, NULL, '', ?, ?)
            """,
            (
                pending["username"],
                pending["phone"],
                pending["email"],
                pending["password_hash"],
                pending["role"],
                now,
                now,
            ),
        )
        user_id = int(cur.lastrowid)
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        raise ValueError("This email is already registered. Please login.")
    finally:
        cur.close()
        conn.close()

    PENDING_VERIFICATIONS.pop(pending_token, None)
    user_row = {
        "id": user_id,
        "username": pending["username"],
        "email": pending["email"],
        "role": pending["role"],
        "created_at": now,
    }
    token = create_auth_session(user_row)

    return {
        "session_token": token,
        "user": user_row,
        "message": "Account verified successfully.",
    }


def action_logout(data: dict[str, Any]) -> dict[str, Any]:
    session_token = str(data.get("session_token") or "").strip()
    if session_token:
        AUTH_SESSIONS.pop(session_token, None)
    return {"message": "Logged out."}


def action_session_resume(data: dict[str, Any]) -> dict[str, Any]:
    session_token = str(data.get("session_token") or "").strip()
    session_info, err = require_retailer_session(session_token)
    if not session_info:
        raise ValueError(err)

    return {
        "session_token": session_token,
        "user": {
            "id": int(session_info["user_id"]),
            "username": str(session_info.get("username") or "Retailer"),
            "email": str(session_info.get("email") or ""),
            "role": str(session_info.get("role") or "retailer"),
        },
    }


def action_retailer_dashboard(data: dict[str, Any]) -> dict[str, Any]:
    session_info, err = require_retailer_session(str(data.get("session_token") or ""))
    if not session_info:
        raise ValueError(err)

    user_id = int(session_info["user_id"])
    conn = sqlite_connect_rw()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) AS cnt FROM products WHERE retailer_id=? AND is_active=1", (user_id,))
    product_count = int(cur.fetchone()[0] or 0)

    cur.execute(
        """
        SELECT COUNT(DISTINCT o.id) AS cnt
        FROM orders o
        JOIN order_items oi ON oi.order_id = o.id
        WHERE oi.retailer_id = ?
        """,
        (user_id,),
    )
    order_count = int(cur.fetchone()[0] or 0)

    cur.execute(
        """
        SELECT COALESCE(SUM(oi.quantity * oi.unit_price), 0) AS revenue
        FROM order_items oi
        JOIN orders o ON o.id = oi.order_id
        WHERE oi.retailer_id = ? AND o.status != 'cancelled'
        """,
        (user_id,),
    )
    revenue = float(cur.fetchone()[0] or 0)

    cur.execute(
        """
        SELECT COUNT(DISTINCT o.id) AS cnt
        FROM orders o
        JOIN order_items oi ON oi.order_id = o.id
        WHERE oi.retailer_id = ? AND o.status = 'pending'
        """,
        (user_id,),
    )
    pending_count = int(cur.fetchone()[0] or 0)

    cur.execute(
        """
        SELECT o.id, o.created_at, o.status, o.total_amount, o.shipping_name,
               GROUP_CONCAT(oi.product_name, ', ') AS items_summary
        FROM orders o
        JOIN order_items oi ON oi.order_id = o.id
        WHERE oi.retailer_id = ?
        GROUP BY o.id, o.created_at, o.status, o.total_amount, o.shipping_name
        ORDER BY o.created_at DESC
        LIMIT 10
        """,
        (user_id,),
    )
    recent_orders = [dict(row) for row in cur.fetchall()]

    cur.execute(
        """
        SELECT p.id, p.name, p.price, p.stock,
               COALESCE(SUM(oi.quantity), 0) AS sold
        FROM products p
        LEFT JOIN order_items oi ON oi.product_id = p.id
        WHERE p.retailer_id = ?
        GROUP BY p.id, p.name, p.price, p.stock
        ORDER BY sold DESC, p.id DESC
        LIMIT 5
        """,
        (user_id,),
    )
    top_products = [dict(row) for row in cur.fetchall()]

    cur.close()
    conn.close()

    return {
        "stats": {
            "product_count": product_count,
            "order_count": order_count,
            "revenue": revenue,
            "pending_count": pending_count,
        },
        "recent_orders": recent_orders,
        "top_products": top_products,
    }


def action_retailer_products(data: dict[str, Any]) -> dict[str, Any]:
    session_info, err = require_retailer_session(str(data.get("session_token") or ""))
    if not session_info:
        raise ValueError(err)

    user_id = int(session_info["user_id"])
    conn = sqlite_connect_rw()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT p.id, p.retailer_id, p.category_id, p.name, p.description, p.price, p.original_price,
               p.stock, p.image_url, p.sku, p.weight_grams, p.is_active, p.created_at, p.updated_at,
               c.name AS category_name,
               (SELECT COALESCE(SUM(oi.quantity), 0) FROM order_items oi WHERE oi.product_id = p.id) AS total_sold
        FROM products p
        LEFT JOIN categories c ON c.id = p.category_id
        WHERE p.retailer_id = ?
        ORDER BY p.created_at DESC
        """,
        (user_id,),
    )
    products = [dict(row) for row in cur.fetchall()]

    cur.execute("SELECT id, name FROM categories ORDER BY name")
    categories = [dict(row) for row in cur.fetchall()]

    cur.close()
    conn.close()
    return {"products": products, "categories": categories}


def action_retailer_add_product(data: dict[str, Any]) -> dict[str, Any]:
    session_info, err = require_retailer_session(str(data.get("session_token") or ""))
    if not session_info:
        raise ValueError(err)

    name = str(data.get("name") or "").strip()
    if not name:
        raise ValueError("Product name is required.")

    user_id = int(session_info["user_id"])
    description = str(data.get("description") or "").strip()
    price = parse_float(data.get("price"), 0.0)
    original_price = data.get("original_price")
    original_price = parse_float(original_price, 0.0) if str(original_price or "").strip() else None
    stock = max(0, parse_int(data.get("stock"), 0))
    category_id = data.get("category_id")
    category_id = parse_int(category_id, 0) if str(category_id or "").strip() else None
    is_active = 1 if parse_int(data.get("is_active"), 1) else 0
    sku = str(data.get("sku") or "").strip() or None
    weight_grams = data.get("weight_grams")
    weight_grams = parse_int(weight_grams, 0) if str(weight_grams or "").strip() else None
    image_url = str(data.get("image_url") or data.get("image_data") or "").strip() or None
    now = sql_now_str()

    conn = sqlite_connect_rw()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO products
        (retailer_id, category_id, name, description, price, original_price, stock,
         image_url, sku, weight_grams, is_active, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            category_id,
            name,
            description,
            price,
            original_price,
            stock,
            image_url,
            sku,
            weight_grams,
            is_active,
            now,
            now,
        ),
    )
    product_id = int(cur.lastrowid)
    conn.commit()
    cur.close()
    conn.close()

    return {"product_id": product_id, "message": "Product added."}


def action_retailer_edit_product(data: dict[str, Any]) -> dict[str, Any]:
    session_info, err = require_retailer_session(str(data.get("session_token") or ""))
    if not session_info:
        raise ValueError(err)

    user_id = int(session_info["user_id"])
    product_id = parse_int(data.get("product_id"), 0)
    if not product_id:
        raise ValueError("Invalid product id.")

    conn = sqlite_connect_rw()
    cur = conn.cursor()
    cur.execute("SELECT id, image_url FROM products WHERE id=? AND retailer_id=?", (product_id, user_id))
    existing = cur.fetchone()
    if not existing:
        cur.close()
        conn.close()
        raise ValueError("Product not found.")

    name = str(data.get("name") or "").strip()
    if not name:
        cur.close()
        conn.close()
        raise ValueError("Product name is required.")

    description = str(data.get("description") or "").strip()
    price = parse_float(data.get("price"), 0.0)
    original_price = data.get("original_price")
    original_price = parse_float(original_price, 0.0) if str(original_price or "").strip() else None
    stock = max(0, parse_int(data.get("stock"), 0))
    category_id = data.get("category_id")
    category_id = parse_int(category_id, 0) if str(category_id or "").strip() else None
    is_active = 1 if parse_int(data.get("is_active"), 1) else 0
    sku = str(data.get("sku") or "").strip() or None
    weight_grams = data.get("weight_grams")
    weight_grams = parse_int(weight_grams, 0) if str(weight_grams or "").strip() else None
    image_url = str(data.get("image_url") or data.get("image_data") or "").strip() or existing["image_url"]
    now = sql_now_str()

    cur.execute(
        """
        UPDATE products
        SET name=?, description=?, price=?, original_price=?, stock=?, category_id=?,
            is_active=?, image_url=?, sku=?, weight_grams=?, updated_at=?
        WHERE id=? AND retailer_id=?
        """,
        (
            name,
            description,
            price,
            original_price,
            stock,
            category_id,
            is_active,
            image_url,
            sku,
            weight_grams,
            now,
            product_id,
            user_id,
        ),
    )
    conn.commit()
    cur.close()
    conn.close()
    return {"message": "Product updated."}


def action_retailer_delete_product(data: dict[str, Any]) -> dict[str, Any]:
    session_info, err = require_retailer_session(str(data.get("session_token") or ""))
    if not session_info:
        raise ValueError(err)

    product_id = parse_int(data.get("product_id"), 0)
    if not product_id:
        raise ValueError("Invalid product id.")

    conn = sqlite_connect_rw()
    cur = conn.cursor()
    cur.execute(
        "UPDATE products SET is_active=0, updated_at=? WHERE id=? AND retailer_id=?",
        (sql_now_str(), product_id, int(session_info["user_id"])),
    )
    conn.commit()
    cur.close()
    conn.close()
    return {"message": "Product removed from shop."}


def action_retailer_orders(data: dict[str, Any]) -> dict[str, Any]:
    session_info, err = require_retailer_session(str(data.get("session_token") or ""))
    if not session_info:
        raise ValueError(err)

    status_filter = str(data.get("status") or "").strip().lower()
    user_id = int(session_info["user_id"])
    conn = sqlite_connect_rw()
    cur = conn.cursor()

    sql = (
        """
        SELECT o.id, o.created_at, o.status, o.total_amount, o.shipping_name, o.shipping_address, o.shipping_phone,
               GROUP_CONCAT(oi.product_name || ' x' || oi.quantity, ', ') AS items_summary,
               ROUND(SUM(oi.quantity * oi.unit_price), 2) AS retailer_total
        FROM orders o
        JOIN order_items oi ON oi.order_id = o.id
        WHERE oi.retailer_id = ?
        """
    )
    params: list[Any] = [user_id]
    if status_filter:
        sql += " AND o.status = ?"
        params.append(status_filter)
    sql += " GROUP BY o.id, o.created_at, o.status, o.total_amount, o.shipping_name, o.shipping_address, o.shipping_phone"
    sql += " ORDER BY o.created_at DESC"

    cur.execute(sql, params)
    orders = [dict(row) for row in cur.fetchall()]
    cur.close()
    conn.close()
    return {"orders": orders, "status_filter": status_filter}


def action_retailer_update_order_status(data: dict[str, Any]) -> dict[str, Any]:
    session_info, err = require_retailer_session(str(data.get("session_token") or ""))
    if not session_info:
        raise ValueError(err)

    order_id = parse_int(data.get("order_id"), 0)
    new_status = str(data.get("status") or "").strip().lower()
    allowed = {"pending", "processing", "shipped", "delivered", "cancelled"}
    if order_id <= 0 or new_status not in allowed:
        raise ValueError("Invalid order status update request.")

    conn = sqlite_connect_rw()
    cur = conn.cursor()
    cur.execute(
        "SELECT id FROM order_items WHERE order_id=? AND retailer_id=? LIMIT 1",
        (order_id, int(session_info["user_id"])),
    )
    authorized = cur.fetchone()
    if not authorized:
        cur.close()
        conn.close()
        raise ValueError("You are not allowed to update this order.")

    cur.execute("UPDATE orders SET status=?, updated_at=? WHERE id=?", (new_status, sql_now_str(), order_id))
    conn.commit()
    cur.close()
    conn.close()
    return {"status": new_status, "message": "Order status updated."}


def action_retailer_profile(data: dict[str, Any]) -> dict[str, Any]:
    session_info, err = require_retailer_session(str(data.get("session_token") or ""))
    if not session_info:
        raise ValueError(err)

    user_id = int(session_info["user_id"])
    conn = sqlite_connect_rw()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, username, email, phone_number, role, profile_pic, bio, created_at
        FROM users
        WHERE id = ?
        """,
        (user_id,),
    )
    user = cur.fetchone()
    cur.close()
    conn.close()
    if not user:
        raise ValueError("User not found.")

    return {"user": dict(user)}


def action_retailer_profile_update(data: dict[str, Any]) -> dict[str, Any]:
    session_info, err = require_retailer_session(str(data.get("session_token") or ""))
    if not session_info:
        raise ValueError(err)

    username = str(data.get("username") or "").strip()
    phone = str(data.get("phone_number") or data.get("phone") or "").strip()
    bio = str(data.get("bio") or "").strip()[:500]
    if not username:
        raise ValueError("Username is required.")

    user_id = int(session_info["user_id"])
    conn = sqlite_connect_rw()
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET username=?, phone_number=?, bio=?, updated_at=? WHERE id=?",
        (username[:80], phone[:30], bio, sql_now_str(), user_id),
    )
    conn.commit()
    cur.close()
    conn.close()

    session_info["username"] = username[:80]
    return {"username": username[:80], "message": "Profile updated."}


def encode_structured_bot_message(payload: dict[str, Any]) -> str:
    body: dict[str, Any] = {"_sage_structured": True, "answer": str(payload.get("answer") or "")}
    if SHOW_DEBUG_DETAILS:
        body["query_ran"] = str(payload.get("query_ran") or "")
        body["db_output"] = str(payload.get("db_output") or "")
        body["status"] = str(payload.get("status") or "")
    return json.dumps(body, ensure_ascii=True)


def decode_structured_bot_message(raw_message: str) -> Any:
    if not raw_message:
        return ""
    try:
        payload = json.loads(raw_message)
    except Exception:
        return raw_message

    if isinstance(payload, dict) and payload.get("_sage_structured"):
        if SHOW_DEBUG_DETAILS:
            return {
                "answer": payload.get("answer", ""),
                "query_ran": payload.get("query_ran", ""),
                "db_output": payload.get("db_output", ""),
                "status": payload.get("status", ""),
            }
        return payload.get("answer", "")
    return raw_message


def format_history_ts(raw_created_at: Any) -> str:
    if raw_created_at is None:
        return ""
    txt = str(raw_created_at).strip()
    if not txt:
        return ""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(txt[:19], fmt)
            return dt.strftime("%d %b %H:%M")
        except Exception:
            continue
    return txt[:16]


def insert_ai_chat_row(
    conn: sqlite3.Connection,
    user_id: int,
    role: str,
    sender: str,
    message: str,
) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO ai_chat_history (user_id, role, sender, message, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            int(user_id),
            (role or "customer").strip().lower()[:20] or "customer",
            "user" if str(sender).lower() == "user" else "bot",
            message,
            sql_now_str(),
        ),
    )
    cur.close()


def action_assistant_chat(data: dict[str, Any]) -> dict[str, Any]:
    session_info, err = require_retailer_session(str(data.get("session_token") or ""))
    if not session_info:
        raise ValueError(err)

    message = str(data.get("message") or "").strip()
    if not message:
        raise ValueError("Please type a message first.")

    answer, sql_text, db_output, status = process_question_with_mode(message, "Retailer")
    bot_payload: dict[str, Any] = {"answer": answer}
    if SHOW_DEBUG_DETAILS:
        bot_payload["query_ran"] = sql_text
        bot_payload["db_output"] = db_output
        bot_payload["status"] = status

    conn = sqlite_connect_rw()
    try:
        insert_ai_chat_row(conn, int(session_info["user_id"]), "retailer", "user", message)
        insert_ai_chat_row(conn, int(session_info["user_id"]), "retailer", "bot", encode_structured_bot_message(bot_payload))
        conn.commit()
    finally:
        conn.close()

    return {"reply": bot_payload}


def action_assistant_history(data: dict[str, Any]) -> dict[str, Any]:
    session_info, err = require_retailer_session(str(data.get("session_token") or ""))
    if not session_info:
        raise ValueError(err)

    user_id = int(session_info["user_id"])
    conn = sqlite_connect_rw()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT sender, message, created_at
        FROM ai_chat_history
        WHERE user_id = ? AND role = 'retailer'
        ORDER BY id DESC
        LIMIT 40
        """,
        (user_id,),
    )
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()

    rows.reverse()
    history: list[dict[str, Any]] = []
    for row in rows:
        sender = str(row.get("sender") or "bot").lower()
        message = row.get("message") or ""
        if sender == "bot":
            message = decode_structured_bot_message(str(message))
        history.append(
            {
                "sender": "user" if sender == "user" else "bot",
                "message": message,
                "created_at": format_history_ts(row.get("created_at")),
            }
        )

    return {"history": history}


# ============================================================
# [SECTION: BRIDGE DISPATCH]
# ============================================================
def bridge_response(request_id: str, action: str, ok: bool, **kwargs: Any) -> str:
    payload: dict[str, Any] = {
        "request_id": request_id,
        "action": action,
        "ok": ok,
        "ts": datetime.utcnow().isoformat(),
    }
    payload.update(kwargs)
    return json.dumps(payload, ensure_ascii=True)


def parse_bridge_payload(raw_payload: str) -> tuple[str, dict[str, Any]]:
    raw = (raw_payload or "").strip()
    if not raw:
        return "", {}

    obj = json.loads(raw)
    if not isinstance(obj, dict):
        raise ValueError("Bridge payload must be a JSON object.")

    request_id = str(obj.get("request_id") or "")
    data = obj.get("data") or {}
    if not isinstance(data, dict):
        raise ValueError("Bridge payload data must be a JSON object.")
    return request_id, data


def bridge_dispatch(action: str, payload_json: str) -> str:
    action_name = (action or "").strip().lower()
    request_id = ""
    try:
        request_id, data = parse_bridge_payload(payload_json)

        handlers = {
            "login": action_login,
            "register": action_register,
            "verify_otp": action_verify_otp,
            "logout": action_logout,
            "session_resume": action_session_resume,
            "retailer_dashboard": action_retailer_dashboard,
            "retailer_products": action_retailer_products,
            "retailer_add_product": action_retailer_add_product,
            "retailer_edit_product": action_retailer_edit_product,
            "retailer_delete_product": action_retailer_delete_product,
            "retailer_orders": action_retailer_orders,
            "retailer_update_order_status": action_retailer_update_order_status,
            "retailer_profile": action_retailer_profile,
            "retailer_profile_update": action_retailer_profile_update,
            "assistant_chat": action_assistant_chat,
            "assistant_history": action_assistant_history,
        }
        handler = handlers.get(action_name)
        if not handler:
            raise ValueError(f"Unknown bridge action: {action_name}")

        data_out = handler(data)
        return bridge_response(request_id, action_name, True, data=data_out)
    except Exception as exc:
        return bridge_response(request_id, action_name or "unknown", False, error=str(exc))


# ============================================================
# [SECTION: FRONTEND]
# ============================================================
APP_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=Manrope:wght@400;500;600;700;800&display=swap');
@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&display=swap');

:root {
    --rp-bg: #f7f4ea;
    --rp-bg-strong: #efe8d1;
    --rp-panel: #fffdf8;
    --rp-ink: #1f2a2e;
    --rp-ink-soft: #58656b;
    --rp-line: #e6ddc8;
    --rp-accent: #0c7a6a;
    --rp-accent-2: #e4a72c;
    --rp-danger: #c44b3b;
    --rp-success: #1f9152;
    --rp-shadow: 0 22px 40px rgba(34, 29, 16, 0.08);
}

*,
*::before,
*::after {
    box-sizing: border-box;
}

html,
body,
.gradio-container {
    height: 100%;
    margin: 0 !important;
    padding: 0 !important;
    background: var(--rp-bg) !important;
    color: var(--rp-ink);
    font-family: 'Manrope', sans-serif !important;
}

.gradio-container {
    max-width: none !important;
    width: 100% !important;
    padding: 0 !important;
}

.gradio-container .main,
.gradio-container .wrap,
.gradio-container .contain,
.gradio-container .prose,
.gradio-container .app {
    max-width: none !important;
}

#bridge-zone {
    position: absolute !important;
    left: -10000px !important;
    top: 0 !important;
    width: 1px !important;
    height: 1px !important;
    overflow: hidden !important;
    opacity: 0 !important;
    pointer-events: none !important;
}

    :root {
        --charcoal: #1A1C23;
        --muted: #8E9BAE;
        --sand: #E1D9BC;
        --ivory: #F0F0DB;
        --accent:#1A1C23;
        --retailer: #1A1C23;
        --card-radius: 40px;
        --shadow: 0 30px 60px rgba(0, 0, 0, 0.12);
    }

    * {
        box-sizing: border-box;
        margin: 0;
        padding: 0;
    }

    #rp-auth-view * {
        font-family: 'Plus Jakarta Sans', sans-serif !important;
    }

    html,
    body {
        height: 100%;
        overflow: hidden;
    }

    .fullscreen-wrapper {
        width: 100%;
        min-height: 100vh;
        display: flex;
        align-items: center;
        justify-content: center;
        background: linear-gradient(135deg, var(--ivory) 0%, #FFFFFF 50%, var(--sand) 100%);
        position: relative;
        color: var(--charcoal) !important;
        overflow: hidden;
    }

    /* BACKGROUND GLOW */
    .bg-glow {
        position: absolute;
        width: 600px;
        height: 600px;
        background: radial-gradient(circle, var(--sand) 0%, transparent 70%);
        filter: blur(100px);
        opacity: 0.3;
        z-index: 1;
        animation: floatGlow 8s ease-in-out infinite alternate;
    }

    @keyframes floatGlow {
        0%  { transform: translate(-250px, 150px); }
        100%{ transform: translate(250px, -150px); }
    }

    #rp-auth-view {
        display: none;
    }

    #rp-auth-view.active {
        display: flex;
    }

    .container {
        width: 90%;
        max-width: 840px;
        height: 540px;
        display: flex;
        border-radius: var(--card-radius);
        background: rgba(255, 255, 255, 0.6);
        backdrop-filter: blur(25px);
        -webkit-backdrop-filter: blur(25px);
        border: 1px solid rgba(255, 255, 255, 0.8);
        box-shadow: var(--shadow);
        z-index: 10;
        overflow: hidden;
        position: relative;
    }

    /* LEFT PANEL */
    .left {
        width: 42%;
        padding: 40px;
        display: flex;
        flex-direction: column;
        justify-content: center;
        background: linear-gradient(160deg, rgba(225, 217, 188, 0.2) 0%, transparent 100%);
    }

    .text-stage {
        min-height: 160px;
        display: flex;
        flex-direction: column;
        justify-content: center;
        margin-bottom: 20px;
        transition: opacity 0.4s ease, transform 0.4s ease;
    }

    .text-stage.fade-out {
        opacity: 0;
        transform: translateY(20px);
    }

    .text-stage.fade-in {
        opacity: 1;
        transform: translateY(0);
    }

    .left h1 {
        font-size: 34px;
        font-weight: 800;
        margin-bottom: 12px;
        line-height: 1.1;
    }

    .left p {
        font-size: 14px;
        color: rgba(26, 28, 35, 0.7);
        line-height: 1.6;
    }

    /* Role indicator pill on left panel */
    .left-role-pill {
        display: inline-flex;
        align-items: center;
        gap: 7px;
        margin-top: 14px;
        padding: 6px 14px;
        border-radius: 20px;
        font-size: 12px;
        font-weight: 700;
        width: fit-content;
        transition: all 0.3s;
    }

    .left-role-pill.customer {
        background: rgba(26,28,35,0.1);
        color: var(--accent);
        border: 1px solid rgba(26,28,35,0.2);
    }

    .left-role-pill.retailer {
        background: rgba(26,28,35,0.10);
        color: var(--retailer);
        border: 1px solid rgba(26,28,35,0.10);
    }

    .pill-dot {
        width: 7px;
        height: 7px;
        border-radius: 50%;
        flex-shrink: 0;
    }

    .left-role-pill.customer .pill-dot { background: var(--accent); }
    .left-role-pill.retailer .pill-dot { background: var(--retailer); }

    .btn-toggle {
        padding: 12px 24px;
        border-radius: 14px;
        background: var(--charcoal);
        color: #fff;
        border: none;
        cursor: pointer;
        font-weight: 700;
        font-size: 13px;
        width: fit-content;
        transform: translateY(0);
        backface-visibility: hidden;
        will-change: transform;
        transition: background-color 0.25s ease, box-shadow 0.25s ease, transform 0.25s ease;
    }

    .btn-toggle:hover {
        transform: translateY(-1px);
        box-shadow: 0 8px 18px rgba(0, 0, 0, 0.12);
    }

    /* RIGHT PANEL */
    .right {
        width: 58%;
        padding: 40px;
        display: flex;
        align-items: center;
        justify-content: center;
        background: #fff;
    }

    .card {
        width: 100%;
        max-width: 340px;
        display: flex;
        flex-direction: column;
    }

    .brand-section {
        display: flex;
        align-items: center;
        gap: 12px;
        margin-bottom: 25px;
    }

    .brand-section img {
        width: 35px;
        height: 35px;
    }

    .brand-section h3 {
        margin: 0;
        font-size: 18px;
        font-weight: 800;
        letter-spacing: 1px;
    }

    /* TABS */
    .tab-nav {
        display: flex;
        background: #F4F5F0;
        padding: 5px;
        border-radius: 16px;
        margin-bottom: 18px;
    }

    .tab-item {
        flex: 1;
        text-align: center;
        padding: 10px;
        border-radius: 12px;
        cursor: pointer;
        font-weight: 700;
        font-size: 12px;
        color: var(--muted);
        transform: translateY(0);
        backface-visibility: hidden;
        will-change: transform;
        transition: background-color 0.25s ease, color 0.25s ease, box-shadow 0.25s ease, transform 0.25s ease;
    }

    .tab-item.active {
        background: #fff;
        color: var(--charcoal);
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.05);
    }

    /* ROLE SELECTOR - identical to tab-nav/tab-item */
    .role-section-label {
        font-size: 10px;
        font-weight: 800;
        text-transform: uppercase;
        letter-spacing: 0.5px;
        color: var(--muted);
        margin-bottom: 6px;
    }

    .role-selector {
        display: flex;
        background: #F4F5F0;
        padding: 5px;
        border-radius: 16px;
        margin-bottom: 14px;
        gap: 0;
    }

    .role-sel-btn {
        flex: 1;
        text-align: center;
        padding: 10px;
        border-radius: 12px;
        border: none;
        background: transparent;
        cursor: pointer;
        font-weight: 700;
        font-size: 12px;
        color: var(--muted);
        transform: translateY(0);
        backface-visibility: hidden;
        will-change: transform;
        transition: background-color 0.25s ease, color 0.25s ease, box-shadow 0.25s ease, transform 0.25s ease;
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 7px;
    }

    .role-sel-btn .rdot {
        width: 7px;
        height: 7px;
        border-radius: 50%;
        background: #C9D0DB;
        flex-shrink: 0;
        transition: 0.2s;
    }

    /* Customer selected */
    .role-sel-btn.sel-customer {
        background: #fff;
        color: var(--accent);
        box-shadow: 0 4px 12px rgba(0,0,0,0.05);
    }

    .role-sel-btn.sel-customer .rdot {
        background: var(--accent);
    }

    /* Retailer selected */
    .role-sel-btn.sel-retailer {
        background: #fff;
        color: var(--retailer);
        box-shadow: 0 4px 12px rgba(0,0,0,0.05);
    }

    .role-sel-btn.sel-retailer .rdot {
        background: var(--retailer);
    }

    /* SLIDER */
    .overflow-wrapper {
        overflow: hidden;
        width: 100%;
        padding-bottom: 10px;
    }

    .form-slider {
        display: flex;
        width: 200%;
        transition: transform 0.5s cubic-bezier(.22, .9, .3, 1);
    }

    .form-page {
        flex: 0 0 50%;
        padding: 0 10px 10px 10px;
    }

    /* INPUTS */
    .input-row {
        display: flex;
        gap: 12px;
    }

    .input-group {
        margin-bottom: 11px;
        flex: 1;
    }

    .input-group label {
        display: block;
        font-size: 10px;
        font-weight: 800;
        text-transform: uppercase;
        margin-bottom: 6px;
        color: var(--muted);
        letter-spacing: 0.5px;
    }

    .input-wrapper {
        position: relative;
        display: flex;
        align-items: center;
    }

    .input-group input {
        width: 100%;
        padding: 14px 16px;
        border-radius: 14px;
        border: 1px solid #EAEBE6;
        background: #F9FAFB;
        font-size: 14px;
        color: var(--charcoal);
        outline: none;
        transition: 0.2s;
    }

    .input-group input:focus {
        border-color: var(--sand);
        background: #fff;
        box-shadow: 0 0 0 2px rgba(225, 217, 188, 0.4);
    }

    .pass-toggle {
        position: absolute;
        right: 14px;
        cursor: pointer;
        font-size: 10px;
        font-weight: 800;
        color: var(--muted);
        user-select: none;
    }

    /* BUTTON */
    .primary-btn {
        width: 100%;
        padding: 15px;
        border-radius: 16px;
        background: var(--charcoal);
        color: #fff;
        border: none;
        font-weight: 700;
        font-size: 14px;
        cursor: pointer;
        margin-top: 10px;
        box-shadow: 0 10px 20px rgba(0, 0, 0, 0.1);
        min-height: 52px;
        transform: translateY(0);
        backface-visibility: hidden;
        will-change: transform;
        transition: background-color 0.25s ease, box-shadow 0.25s ease, transform 0.25s ease;
    }

    .primary-btn:hover {
        transform: translateY(-1px);
        box-shadow: 0 14px 24px rgba(0, 0, 0, 0.14);
    }

    .primary-btn.retailer-btn {
        background: linear-gradient(135deg, #1A1C23, #1A1C23);
    }

    .form-error {
        background: rgba(220, 53, 69, 0.09);
        color: #c0392b;
        border-radius: 8px;
        padding: 7px 12px;
        font-size: 11px;
        font-weight: 600;
        margin-bottom: 8px;
        line-height: 1.4;
    }

    /* MOBILE */
    @media (max-width: 850px) {
        .container {
          flex-direction: column;
          height: auto;
          max-height: 97vh;
          width: 85%;
          border-radius: 32px;
          margin: 0;
        }

        .right {
          width: 100%;
          padding: 22px 22px 12px 22px;
          order: 1;
          flex: 0;
          border-radius: 32px 32px 0 0;
        }

        .left {
          width: 100%;
          padding: 10px 22px 18px 22px;
          text-align: center;
          order: 2;
          flex: 0;
          background: linear-gradient(180deg, rgba(255, 255, 255, 0) 0%, rgba(225, 217, 188, 0.1) 100%);
          border-radius: 0 0 32px 32px;
        }

        .text-stage { min-height: auto; margin-bottom: 5px; }
        .left h1 { font-size: 15px; margin-bottom: 2px; }
        .left p { font-size: 10px; margin-bottom: 4px; line-height: 1.3; }
        .left-role-pill { margin: 4px auto; }
        .btn-toggle { margin: 8px auto 0; padding: 7px 16px; font-size: 11px; }
        .brand-section { margin-bottom: 12px; justify-content: center; gap: 8px; }
        .brand-section img { width: 24px; height: 24px; }
        .brand-section h3 { font-size: 13px; }
        .tab-nav { margin-bottom: 14px; padding: 4px; }
        .tab-item { padding: 8px; font-size: 11px; }
        .role-selector { gap: 8px; margin-bottom: 10px; }
        .role-sel-btn { padding: 9px 6px; font-size: 12px; }
        .input-group { margin-bottom: 10px; }
        .input-group input { padding: 12px 14px; font-size: 13px; border-radius: 12px; }
        .input-group label { font-size: 9px; margin-bottom: 3px; }
        .primary-btn { padding: 12px; font-size: 14px; min-height: 48px; margin-top: 5px; box-shadow: none; }
        .input-row { flex-direction: column; gap: 0; }
        .overflow-wrapper { padding-bottom: 5px; }
    }

    @media (max-height: 620px) and (max-width: 850px) {
        .container { max-height: 98vh; }
    }

.rp-shell {
    min-height: 100vh;
    width: 100%;
    position: relative;
    overflow: hidden;
    background: radial-gradient(circle at 92% 10%, #f2d99c 0, transparent 30%),
                radial-gradient(circle at 8% 90%, #b2d8c2 0, transparent 32%),
                var(--rp-bg);
}

.rp-shell::before,
.rp-shell::after {
    content: "";
    position: absolute;
    pointer-events: none;
    z-index: 0;
}

.rp-shell::before {
    width: 340px;
    height: 340px;
    border-radius: 38% 62% 56% 44% / 57% 40% 60% 43%;
    background: rgba(12, 122, 106, 0.08);
    left: -120px;
    top: -80px;
}

.rp-shell::after {
    width: 380px;
    height: 380px;
    border-radius: 68% 32% 45% 55% / 40% 63% 37% 60%;
    background: rgba(228, 167, 44, 0.12);
    right: -150px;
    bottom: -150px;
}

.rp-auth,
.rp-app {
    position: relative;
    z-index: 2;
}

.rp-auth {
    min-height: 100vh;
    display: none;
    align-items: center;
    justify-content: center;
    padding: 34px 20px;
}

.rp-auth.active {
    display: flex;
}

.rp-auth-wrap {
    width: min(1020px, 100%);
    display: grid;
    grid-template-columns: 1.1fr 1fr;
    background: rgba(255, 255, 255, 0.88);
    border: 1px solid rgba(230, 221, 200, 0.8);
    border-radius: 28px;
    overflow: hidden;
    box-shadow: var(--rp-shadow);
}

.rp-auth-hero {
    padding: 44px;
    background: linear-gradient(135deg, #13272d 0%, #1f4f58 100%);
    color: #fef5de;
    position: relative;
}

.rp-auth-hero h1 {
    margin: 0 0 12px;
    font-family: 'Space Grotesk', sans-serif;
    font-size: clamp(28px, 4vw, 40px);
    line-height: 1.05;
    letter-spacing: -0.02em;
}

.rp-auth-hero p {
    margin: 0;
    max-width: 420px;
    color: rgba(254, 245, 222, 0.86);
    line-height: 1.65;
}

.rp-auth-badges {
    margin-top: 26px;
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
}

.rp-auth-badge {
    border: 1px solid rgba(255, 255, 255, 0.22);
    background: rgba(255, 255, 255, 0.08);
    border-radius: 999px;
    padding: 7px 12px;
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.02em;
}

.rp-auth-card {
    padding: 26px;
    background: rgba(255, 253, 248, 0.9);
}

.rp-brand {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    margin-bottom: 14px;
}

.rp-brand-name {
    font-family: 'Space Grotesk', sans-serif;
    font-size: 24px;
    font-weight: 700;
    letter-spacing: -0.02em;
}

.rp-brand-sub {
    font-size: 12px;
    color: var(--rp-ink-soft);
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-weight: 800;
}

.rp-auth-tabs {
    display: flex;
    gap: 8px;
    background: var(--rp-bg);
    border: 1px solid var(--rp-line);
    border-radius: 14px;
    padding: 6px;
    margin-bottom: 14px;
}

.rp-auth-tab {
    flex: 1;
    border: 0;
    background: transparent;
    border-radius: 10px;
    padding: 10px 12px;
    font-weight: 700;
    color: var(--rp-ink-soft);
    cursor: pointer;
}

.rp-auth-tab.active {
    background: #fff;
    color: var(--rp-ink);
    box-shadow: 0 2px 8px rgba(32, 44, 45, 0.08);
}

.rp-auth-panel {
    display: none;
}

.rp-auth-panel.active {
    display: block;
}

.rp-form-grid {
    display: grid;
    gap: 10px;
}

.rp-label {
    display: block;
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.07em;
    color: var(--rp-ink-soft);
    font-weight: 800;
    margin-bottom: 4px;
}

.rp-input,
.rp-select,
.rp-textarea {
    width: 100%;
    border: 1px solid var(--rp-line);
    background: #fff;
    border-radius: 12px;
    color: var(--rp-ink);
    padding: 11px 12px;
    outline: none;
    font-size: 14px;
    transition: border-color 0.22s ease, box-shadow 0.22s ease;
}

.rp-textarea {
    min-height: 90px;
    resize: vertical;
}

.rp-input:focus,
.rp-select:focus,
.rp-textarea:focus {
    border-color: var(--rp-accent);
    box-shadow: 0 0 0 3px rgba(12, 122, 106, 0.16);
}

.rp-btn,
.rp-btn-ghost,
.rp-btn-danger {
    border: 0;
    border-radius: 12px;
    padding: 11px 14px;
    font-weight: 800;
    font-size: 13px;
    cursor: pointer;
    letter-spacing: 0.01em;
}

.rp-btn {
    background: linear-gradient(90deg, #0b7565 0%, #129b87 100%);
    color: #fff;
}

.rp-btn:hover {
    filter: brightness(0.97);
}

.rp-btn-ghost {
    background: #fff;
    border: 1px solid var(--rp-line);
    color: var(--rp-ink);
}

.rp-btn-danger {
    background: #fff5f4;
    border: 1px solid #f2cbc6;
    color: var(--rp-danger);
}

.rp-auth-msg {
    min-height: 20px;
    margin-top: 8px;
    font-size: 12px;
    line-height: 1.45;
}

.rp-auth-msg.error {
    color: var(--rp-danger);
}

.rp-auth-msg.note {
    color: var(--rp-accent);
}

.rp-app {
    display: none;
    min-height: 100vh;
}

.rp-app.active {
    display: flex;
}

.rp-sidebar {
    width: 246px;
    background: linear-gradient(170deg, #1d2528 0%, #222f31 56%, #263436 100%);
    color: #f4f3ed;
    position: fixed;
    inset: 0 auto 0 0;
    display: flex;
    flex-direction: column;
    z-index: 20;
    border-right: 1px solid rgba(255, 255, 255, 0.08);
}

.rp-sidebar-brand {
    padding: 24px 20px 18px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.12);
}

.rp-sidebar-brand strong {
    font-family: 'Space Grotesk', sans-serif;
    font-size: 24px;
    display: block;
    letter-spacing: -0.03em;
}

.rp-sidebar-brand span {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: rgba(255, 255, 255, 0.6);
    font-weight: 800;
}

.rp-nav {
    padding: 14px;
    flex: 1;
}

.rp-nav-btn {
    width: 100%;
    border: 0;
    border-radius: 14px;
    background: transparent;
    color: rgba(255, 255, 255, 0.72);
    display: flex;
    align-items: center;
    gap: 11px;
    padding: 11px 12px;
    font-weight: 700;
    margin-bottom: 6px;
    text-align: left;
    cursor: pointer;
    transition: transform 0.2s ease, background-color 0.2s ease;
}

.rp-nav-btn:hover {
    transform: translateX(2px);
    background: rgba(255, 255, 255, 0.08);
}

.rp-nav-btn.active {
    background: rgba(228, 167, 44, 0.2);
    color: #fff;
}

.rp-nav-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: rgba(255, 255, 255, 0.3);
}

.rp-nav-btn.active .rp-nav-dot {
    background: #f8d68b;
}

.rp-sidebar-foot {
    border-top: 1px solid rgba(255, 255, 255, 0.12);
    padding: 14px;
}

.rp-user {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 10px;
}

.rp-user-avatar {
    width: 36px;
    height: 36px;
    border-radius: 50%;
    background: rgba(255, 255, 255, 0.12);
    display: grid;
    place-items: center;
    font-weight: 800;
}

.rp-user-meta strong {
    display: block;
    font-size: 13px;
}

.rp-user-meta span {
    display: block;
    font-size: 11px;
    color: rgba(255, 255, 255, 0.6);
}

.rp-main {
    margin-left: 246px;
    flex: 1;
    padding: 18px;
    min-width: 0;
}

.rp-top {
    background: rgba(255, 253, 248, 0.86);
    border: 1px solid rgba(230, 221, 200, 0.75);
    border-radius: 18px;
    padding: 14px 16px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 14px;
    box-shadow: var(--rp-shadow);
}

.rp-title {
    font-family: 'Space Grotesk', sans-serif;
    font-size: 26px;
    letter-spacing: -0.03em;
    margin: 0;
}

.rp-subtitle {
    margin: 2px 0 0;
    color: var(--rp-ink-soft);
    font-size: 13px;
}

.rp-views {
    margin-top: 14px;
}

.rp-view {
    display: none;
}

.rp-view.active {
    display: block;
    animation: rpFadeUp 0.25s ease both;
}

@keyframes rpFadeUp {
    from {
        opacity: 0;
        transform: translateY(10px);
    }
    to {
        opacity: 1;
        transform: translateY(0);
    }
}

.rp-grid-stats {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 10px;
}

.rp-stat-card,
.rp-panel {
    background: rgba(255, 253, 248, 0.88);
    border: 1px solid rgba(230, 221, 200, 0.75);
    border-radius: 16px;
    box-shadow: var(--rp-shadow);
}

.rp-stat-card {
    padding: 16px;
}

.rp-stat-label {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--rp-ink-soft);
    font-weight: 800;
}

.rp-stat-value {
    margin-top: 8px;
    font-family: 'Space Grotesk', sans-serif;
    font-size: 28px;
    letter-spacing: -0.03em;
}

.rp-panel {
    margin-top: 10px;
    padding: 14px;
}

.rp-panel-title {
    font-family: 'Space Grotesk', sans-serif;
    margin: 0 0 10px;
    font-size: 18px;
    letter-spacing: -0.02em;
}

.rp-table-wrap {
    width: 100%;
    overflow-x: auto;
}

.rp-table {
    width: 100%;
    border-collapse: collapse;
    min-width: 640px;
}

.rp-table th,
.rp-table td {
    border-bottom: 1px solid var(--rp-line);
    padding: 10px 8px;
    text-align: left;
    font-size: 13px;
    vertical-align: top;
}

.rp-table th {
    color: var(--rp-ink-soft);
    text-transform: uppercase;
    font-size: 11px;
    letter-spacing: 0.07em;
}

.rp-products-layout {
    display: grid;
    grid-template-columns: 1.6fr 1fr;
    gap: 10px;
}

.rp-inline-actions {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
}

.rp-orders-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 10px;
    margin-bottom: 10px;
    flex-wrap: wrap;
}

.rp-assistant-wrap {
    background: rgba(255, 253, 248, 0.9);
    border: 1px solid rgba(230, 221, 200, 0.75);
    border-radius: 18px;
    box-shadow: var(--rp-shadow);
    overflow: hidden;
}

.rp-assistant-top {
    padding: 12px 14px;
    border-bottom: 1px solid var(--rp-line);
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
}

.rp-chip {
    border: 1px solid var(--rp-line);
    background: #fff;
    color: var(--rp-ink);
    border-radius: 999px;
    padding: 6px 11px;
    font-size: 12px;
    font-weight: 700;
    cursor: pointer;
}

.rp-chip:hover {
    border-color: var(--rp-accent);
    color: var(--rp-accent);
}

.rp-chat {
    height: 420px;
    overflow-y: auto;
    padding: 14px;
    background: #fbf8ee;
    display: flex;
    flex-direction: column;
    gap: 9px;
}

.rp-msg {
    max-width: min(84%, 760px);
    border-radius: 14px;
    padding: 10px 12px;
    font-size: 13px;
    line-height: 1.55;
    animation: rpMsgIn 0.2s ease both;
    white-space: pre-wrap;
    word-break: break-word;
}

@keyframes rpMsgIn {
    from {
        opacity: 0;
        transform: translateY(6px);
    }
    to {
        opacity: 1;
        transform: translateY(0);
    }
}

.rp-msg.user {
    margin-left: auto;
    background: #1d2d31;
    color: #fff;
    border-radius: 12px 4px 12px 12px;
}

.rp-msg.bot {
    margin-right: auto;
    background: #fff;
    border: 1px solid var(--rp-line);
    color: var(--rp-ink);
    border-radius: 4px 12px 12px 12px;
}

.rp-msg-meta {
    margin-top: 6px;
    font-size: 10px;
    color: #8c9194;
    text-transform: uppercase;
    letter-spacing: 0.08em;
}

.rp-msg-section + .rp-msg-section {
    margin-top: 8px;
    padding-top: 8px;
    border-top: 1px solid var(--rp-line);
}

.rp-msg-title {
    font-size: 10px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--rp-ink-soft);
    font-weight: 800;
}

.rp-msg-pre {
    margin-top: 5px;
    background: #f8f4e8;
    border: 1px solid var(--rp-line);
    border-radius: 9px;
    padding: 8px;
    max-height: 170px;
    overflow: auto;
    white-space: pre-wrap;
}

.rp-assistant-input {
    display: flex;
    gap: 8px;
    padding: 10px;
    border-top: 1px solid var(--rp-line);
    background: #fff;
}

.rp-typing {
    display: inline-flex;
    gap: 4px;
    align-items: center;
}

.rp-typing span {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: #1c2930;
    opacity: 0.5;
    animation: rpDot 1s infinite ease-in-out;
}

.rp-typing span:nth-child(2) {
    animation-delay: 0.12s;
}

.rp-typing span:nth-child(3) {
    animation-delay: 0.24s;
}

@keyframes rpDot {
    0%,
    80%,
    100% {
        transform: scale(0.6);
        opacity: 0.45;
    }
    40% {
        transform: scale(1);
        opacity: 1;
    }
}

.rp-toast-wrap {
    position: fixed;
    right: 16px;
    top: 16px;
    z-index: 90;
    display: flex;
    flex-direction: column;
    gap: 8px;
}

.rp-toast {
    min-width: 220px;
    max-width: 360px;
    border-radius: 10px;
    padding: 10px 12px;
    font-size: 12px;
    font-weight: 700;
    box-shadow: var(--rp-shadow);
    border: 1px solid var(--rp-line);
    background: #fff;
}

.rp-toast.ok {
    border-color: #b9dfc6;
    color: #205f35;
    background: #f4fff7;
}

.rp-toast.err {
    border-color: #f0c6c1;
    color: #842f25;
    background: #fff6f5;
}

.rp-busy {
    position: fixed;
    inset: 0;
    background: rgba(24, 30, 35, 0.16);
    display: none;
    align-items: center;
    justify-content: center;
    z-index: 88;
}

.rp-busy.active {
    display: flex;
}

.rp-busy-box {
    background: #fff;
    border: 1px solid var(--rp-line);
    border-radius: 12px;
    padding: 10px 14px;
    font-size: 13px;
    font-weight: 700;
}

@media (max-width: 1060px) {
    .rp-auth-wrap {
        grid-template-columns: 1fr;
    }

    .rp-grid-stats {
        grid-template-columns: repeat(2, minmax(0, 1fr));
    }

    .rp-products-layout {
        grid-template-columns: 1fr;
    }
}

@media (max-width: 860px) {
    .rp-sidebar {
        width: 72px;
    }

    .rp-sidebar-brand span,
    .rp-nav-btn span,
    .rp-user-meta,
    #rp-logout-btn {
        display: none;
    }

    .rp-main {
        margin-left: 72px;
        padding: 10px;
    }
}

@media (max-width: 640px) {
    .rp-grid-stats {
        grid-template-columns: 1fr;
    }

    .rp-top {
        flex-direction: column;
        align-items: flex-start;
    }

    .rp-chat {
        height: 360px;
    }
}

@media (prefers-reduced-motion: reduce) {
    *,
    *::before,
    *::after {
        animation: none !important;
        transition: none !important;
    }
}
"""


RETAILER_UI_HTML = """
<div id="rp-root" class="rp-shell">
        <div id="rp-auth-view" class="fullscreen-wrapper active">

            <div class="bg-glow"></div>

            <div class="container">

                <div class="left">
                    <div class="text-stage" id="textStage">
                        <h1 id="leftTitle">Hello,<br>Welcome!</h1>
                        <p id="leftDesc">Sign in to continue shopping your favourite items.</p>
                        <div class="left-role-pill customer" id="leftPill">
                            <div class="pill-dot"></div>
                            <span id="pillText">Shopping as Customer</span>
                        </div>
                    </div>
                    <button class="btn-toggle" id="toggleBtn">Home Page</button>
                </div>

                <div class="right">
                    <div class="card">

                        <div class="brand-section">
                            <img src="/static/Images/chit_chat.png" alt="Shopy Logo">
                            <h3>Shopy</h3>
                        </div>

                        <div class="tab-nav">
                            <div class="tab-item active" id="tabLogin">LOGIN</div>
                            <div class="tab-item" id="tabRegister">REGISTER</div>
                        </div>

                        <div class="overflow-wrapper">
                            <div class="form-slider" id="slider">

                                <div class="form-page">
                                    <form method="POST" action="/login">
                                        <div class="role-section-label">I am a</div>
                                        <div class="role-selector">
                                            <button type="button" class="role-sel-btn sel-customer"
                                                            id="lBtnCustomer" onclick="setLoginRole('customer')">
                                                <div class="rdot"></div> Customer
                                            </button>
                                            <button type="button" class="role-sel-btn"
                                                            id="lBtnRetailer" onclick="setLoginRole('retailer')">
                                                <div class="rdot"></div> Retailer
                                            </button>
                                        </div>
                                        <input type="hidden" name="role" id="lRoleInput" value="customer">

                                        <div class="input-group">
                                            <label>Email Address</label>
                                            <input type="email" name="email" placeholder="you@email.com" required>
                                        </div>
                                        <div class="input-group">
                                            <label>Password</label>
                                            <div class="input-wrapper">
                                                <input type="password" name="password" id="loginPass" placeholder="Enter password" required>
                                                <span class="pass-toggle" onclick="togglePassword()">SHOW</span>
                                            </div>
                                        </div>
                                        <button type="submit" class="primary-btn" id="loginBtn">Log In</button>
                                    </form>
                                </div>

                                <div class="form-page">
                                    <form method="POST" action="/register">
                                        <div class="role-section-label">Register as</div>
                                        <div class="role-selector">
                                            <button type="button" class="role-sel-btn sel-customer"
                                                            id="rBtnCustomer" onclick="setRegRole('customer')">
                                                <div class="rdot"></div> Customer
                                            </button>
                                            <button type="button" class="role-sel-btn"
                                                            id="rBtnRetailer" onclick="setRegRole('retailer')">
                                                <div class="rdot"></div> Retailer
                                            </button>
                                        </div>
                                        <input type="hidden" name="role" id="rRoleInput" value="customer">

                                        <div class="input-row">
                                            <div class="input-group">
                                                <label>Username</label>
                                                <input type="text" name="username" placeholder="Username" required>
                                            </div>
                                            <div class="input-group">
                                                <label>Email</label>
                                                <input type="email" name="email" placeholder="you@email.com" required>
                                            </div>
                                        </div>
                                        <div class="input-group">
                                            <label>Set Password</label>
                                            <input type="password" name="password" placeholder="Password" required>
                                        </div>
                                        <button type="submit" class="primary-btn" id="registerBtn">Create Account</button>
                                    </form>
                                </div>

                            </div>
                        </div>

                    </div>
        </div>
            </div>
        </div>

    <section id="rp-app-view" class="rp-app">
        <aside class="rp-sidebar">
            <div class="rp-sidebar-brand">
                <strong>Shopy</strong>
                <span>Retailer A2Z</span>
            </div>
            <nav class="rp-nav">
                <button class="rp-nav-btn active" data-nav="dashboard"><span class="rp-nav-dot"></span><span>Dashboard</span></button>
                <button class="rp-nav-btn" data-nav="products"><span class="rp-nav-dot"></span><span>Products</span></button>
                <button class="rp-nav-btn" data-nav="orders"><span class="rp-nav-dot"></span><span>Orders</span></button>
                <button class="rp-nav-btn" data-nav="assistant"><span class="rp-nav-dot"></span><span>Assistant</span></button>
                <button class="rp-nav-btn" data-nav="profile"><span class="rp-nav-dot"></span><span>Profile</span></button>
            </nav>
            <div class="rp-sidebar-foot">
                <div class="rp-user">
                    <div id="rp-user-avatar" class="rp-user-avatar">R</div>
                    <div class="rp-user-meta">
                        <strong id="rp-sidebar-user">Retailer</strong>
                        <span id="rp-sidebar-email">retailer@shop.com</span>
                    </div>
                </div>
                <button id="rp-logout-btn" class="rp-btn-ghost" type="button" style="width:100%">Logout</button>
            </div>
        </aside>

        <main class="rp-main">
            <div class="rp-top">
                <div>
                    <h2 id="rp-view-title" class="rp-title">Dashboard</h2>
                    <p class="rp-subtitle">Live retailer module running on Gradio bridge</p>
                </div>
                <button id="rp-top-refresh" class="rp-btn-ghost" type="button">Refresh View</button>
            </div>

            <div class="rp-views">
                <section class="rp-view active" data-view="dashboard">
                    <div class="rp-grid-stats">
                        <article class="rp-stat-card">
                            <div class="rp-stat-label">Active Products</div>
                            <div id="rp-stat-products" class="rp-stat-value">0</div>
                        </article>
                        <article class="rp-stat-card">
                            <div class="rp-stat-label">Orders</div>
                            <div id="rp-stat-orders" class="rp-stat-value">0</div>
                        </article>
                        <article class="rp-stat-card">
                            <div class="rp-stat-label">Revenue</div>
                            <div id="rp-stat-revenue" class="rp-stat-value">0</div>
                        </article>
                        <article class="rp-stat-card">
                            <div class="rp-stat-label">Pending</div>
                            <div id="rp-stat-pending" class="rp-stat-value">0</div>
                        </article>
                    </div>

                    <article class="rp-panel">
                        <h3 class="rp-panel-title">Recent Orders</h3>
                        <div class="rp-table-wrap">
                            <table class="rp-table">
                                <thead>
                                    <tr><th>Order</th><th>Customer</th><th>Status</th><th>Items</th><th>Total</th><th>Date</th></tr>
                                </thead>
                                <tbody id="rp-dashboard-orders-body"></tbody>
                            </table>
                        </div>
                    </article>

                    <article class="rp-panel">
                        <h3 class="rp-panel-title">Top Products</h3>
                        <div class="rp-table-wrap">
                            <table class="rp-table">
                                <thead>
                                    <tr><th>Product</th><th>Stock</th><th>Price</th><th>Sold</th></tr>
                                </thead>
                                <tbody id="rp-dashboard-top-body"></tbody>
                            </table>
                        </div>
                    </article>
                </section>

                <section class="rp-view" data-view="products">
                    <div class="rp-products-layout">
                        <article class="rp-panel">
                            <h3 class="rp-panel-title">Your Products</h3>
                            <div class="rp-table-wrap">
                                <table class="rp-table">
                                    <thead>
                                        <tr><th>ID</th><th>Name</th><th>Category</th><th>Price</th><th>Stock</th><th>Status</th><th>Actions</th></tr>
                                    </thead>
                                    <tbody id="rp-products-body"></tbody>
                                </table>
                            </div>
                        </article>

                        <article class="rp-panel">
                            <h3 class="rp-panel-title">Add / Edit Product</h3>
                            <div class="rp-form-grid">
                                <input id="rp-product-id" type="hidden">
                                <div>
                                    <label class="rp-label" for="rp-product-name">Product Name</label>
                                    <input id="rp-product-name" class="rp-input" type="text" placeholder="Product title">
                                </div>
                                <div>
                                    <label class="rp-label" for="rp-product-description">Description</label>
                                    <textarea id="rp-product-description" class="rp-textarea" placeholder="Short product description"></textarea>
                                </div>
                                <div>
                                    <label class="rp-label" for="rp-product-price">Price</label>
                                    <input id="rp-product-price" class="rp-input" type="number" step="0.01" min="0" placeholder="0.00">
                                </div>
                                <div>
                                    <label class="rp-label" for="rp-product-original-price">Original Price</label>
                                    <input id="rp-product-original-price" class="rp-input" type="number" step="0.01" min="0" placeholder="Optional">
                                </div>
                                <div>
                                    <label class="rp-label" for="rp-product-stock">Stock</label>
                                    <input id="rp-product-stock" class="rp-input" type="number" min="0" placeholder="0">
                                </div>
                                <div>
                                    <label class="rp-label" for="rp-product-category">Category</label>
                                    <select id="rp-product-category" class="rp-select"></select>
                                </div>
                                <div>
                                    <label class="rp-label" for="rp-product-sku">SKU</label>
                                    <input id="rp-product-sku" class="rp-input" type="text" placeholder="Optional SKU">
                                </div>
                                <div>
                                    <label class="rp-label" for="rp-product-weight">Weight (grams)</label>
                                    <input id="rp-product-weight" class="rp-input" type="number" min="0" placeholder="Optional">
                                </div>
                                <div>
                                    <label class="rp-label" for="rp-product-image">Image URL / Data</label>
                                    <textarea id="rp-product-image" class="rp-textarea" placeholder="Paste image URL or base64 data"></textarea>
                                </div>
                                <div>
                                    <label class="rp-label" for="rp-product-active">Status</label>
                                    <select id="rp-product-active" class="rp-select">
                                        <option value="1">Active</option>
                                        <option value="0">Inactive</option>
                                    </select>
                                </div>
                                <div class="rp-inline-actions">
                                    <button id="rp-product-save" class="rp-btn" type="button">Save Product</button>
                                    <button id="rp-product-clear" class="rp-btn-ghost" type="button">Clear Form</button>
                                    <button id="rp-product-delete" class="rp-btn-danger" type="button">Deactivate</button>
                                </div>
                            </div>
                        </article>
                    </div>
                </section>

                <section class="rp-view" data-view="orders">
                    <article class="rp-panel">
                        <div class="rp-orders-head">
                            <h3 class="rp-panel-title" style="margin:0">Retailer Orders</h3>
                            <div class="rp-inline-actions">
                                <select id="rp-orders-filter" class="rp-select">
                                    <option value="">All</option>
                                    <option value="pending">Pending</option>
                                    <option value="processing">Processing</option>
                                    <option value="shipped">Shipped</option>
                                    <option value="delivered">Delivered</option>
                                    <option value="cancelled">Cancelled</option>
                                </select>
                                <button id="rp-orders-refresh" class="rp-btn-ghost" type="button">Refresh</button>
                            </div>
                        </div>

                        <div class="rp-table-wrap">
                            <table class="rp-table">
                                <thead>
                                    <tr><th>Order</th><th>Customer</th><th>Items</th><th>Retailer Total</th><th>Status</th><th>Update</th></tr>
                                </thead>
                                <tbody id="rp-orders-body"></tbody>
                            </table>
                        </div>
                    </article>
                </section>

                <section class="rp-view" data-view="assistant">
                    <article class="rp-assistant-wrap">
                        <div class="rp-assistant-top">
                            <button class="rp-chip" data-prompt="Give me today's retailer health check from my current data.">Health Check</button>
                            <button class="rp-chip" data-prompt="Which products are low stock and how much should I reorder?">Low Stock</button>
                            <button class="rp-chip" data-prompt="Show my top revenue products with reasons.">Top Revenue</button>
                            <button class="rp-chip" data-prompt="Analyze pending orders and suggest immediate actions.">Order Risk</button>
                            <button class="rp-chip" data-prompt="Create a 7-day action plan to increase sales.">7-Day Plan</button>
                        </div>
                        <div id="rp-assistant-messages" class="rp-chat"></div>
                        <div class="rp-assistant-input">
                            <input id="rp-assistant-input" class="rp-input" type="text" placeholder="Ask Sage anything about your retailer business..." autocomplete="off">
                            <button id="rp-assistant-send" class="rp-btn" type="button">Send</button>
                        </div>
                    </article>
                </section>

                <section class="rp-view" data-view="profile">
                    <article class="rp-panel">
                        <h3 class="rp-panel-title">Retailer Profile</h3>
                        <div class="rp-form-grid">
                            <div>
                                <label class="rp-label" for="rp-profile-name">Username</label>
                                <input id="rp-profile-name" class="rp-input" type="text">
                            </div>
                            <div>
                                <label class="rp-label" for="rp-profile-email">Email</label>
                                <input id="rp-profile-email" class="rp-input" type="email" readonly>
                            </div>
                            <div>
                                <label class="rp-label" for="rp-profile-phone">Phone</label>
                                <input id="rp-profile-phone" class="rp-input" type="text">
                            </div>
                            <div>
                                <label class="rp-label" for="rp-profile-bio">Bio</label>
                                <textarea id="rp-profile-bio" class="rp-textarea" placeholder="Write a short shop profile"></textarea>
                            </div>
                            <div class="rp-inline-actions">
                                <button id="rp-profile-save" class="rp-btn" type="button">Save Profile</button>
                            </div>
                        </div>
                    </article>
                </section>
            </div>
        </main>
    </section>

    <div id="rp-toast-wrap" class="rp-toast-wrap"></div>
    <div id="rp-busy" class="rp-busy"><div id="rp-busy-text" class="rp-busy-box">Loading...</div></div>
</div>
"""


APP_JS = """
function () {
    let root = document.getElementById("rp-root");

    const VIEW_TITLES = {
        dashboard: "Dashboard",
        products: "Products",
        orders: "Orders",
        assistant: "Assistant",
        profile: "Profile",
    };

    const STATUS_OPTIONS = ["pending", "processing", "shipped", "delivered", "cancelled"];
    const STORAGE_KEY = "shopy_retailer_session_token";

    const state = {
        bridgeAction: null,
        bridgePayloadIn: null,
        bridgeOutput: null,
        bridgeSubmit: null,
        requestSeq: 0,
        bridgeQueue: Promise.resolve(),
        sessionToken: "",
        user: null,
        pendingToken: "",
        currentView: "dashboard",
        loaded: {
            dashboard: false,
            products: false,
            orders: false,
            assistant: false,
            profile: false,
        },
        products: [],
        categories: [],
        orders: [],
        typingNode: null,
    };

    let isRegister = false;
    let lRole = "customer";
    let rRole = "customer";

    function byId(id) {
        return document.getElementById(id);
    }

    function escapeHtml(value) {
        return String(value || "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/\"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    function asText(value) {
        return String(value == null ? "" : value);
    }

    function fmtMoney(value) {
        const num = Number(value || 0);
        return num.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }

    function setBridgeValue(node, value) {
        if (!node) {
            return;
        }
        node.value = value;
        node.dispatchEvent(new Event("input", { bubbles: true }));
        node.dispatchEvent(new Event("change", { bubbles: true }));
    }

    function findBridgeInput(idBase) {
        return (
            document.querySelector("#" + idBase + " textarea") ||
            document.querySelector("#" + idBase + " input") ||
            document.querySelector("textarea#" + idBase) ||
            document.querySelector("input#" + idBase)
        );
    }

    function findBridgeButton(idBase) {
        return (
            document.querySelector("#" + idBase + " button") ||
            document.querySelector("button#" + idBase)
        );
    }

    function ensureBridgeRefs() {
        state.bridgeAction = findBridgeInput("bridge-action");
        state.bridgePayloadIn = findBridgeInput("bridge-payload-in");
        state.bridgeOutput = findBridgeInput("bridge-output");
        state.bridgeSubmit = findBridgeButton("bridge-submit");

        return Boolean(
            state.bridgeAction && state.bridgePayloadIn && state.bridgeOutput && state.bridgeSubmit
        );
    }

    function waitForBridge() {
        return new Promise((resolve, reject) => {
            let tries = 0;
            const loop = () => {
                if (ensureBridgeRefs()) {
                    resolve();
                    return;
                }
                tries += 1;
                if (tries > 80) {
                    reject(new Error("Could not connect frontend bridge."));
                    return;
                }
                setTimeout(loop, 120);
            };
            loop();
        });
    }

    function callBridgeRaw(action, data) {
        return new Promise((resolve, reject) => {
            if (!ensureBridgeRefs()) {
                reject(new Error("Bridge is not ready."));
                return;
            }

            const requestId = String(Date.now()) + "-" + String(++state.requestSeq);
            const payload = JSON.stringify({ request_id: requestId, data: data || {} });

            setBridgeValue(state.bridgeAction, action);
            setBridgeValue(state.bridgePayloadIn, payload);
            setBridgeValue(state.bridgeOutput, "");

            if (!state.bridgeSubmit || typeof state.bridgeSubmit.click !== "function") {
                reject(new Error("Bridge submit button missing."));
                return;
            }
            state.bridgeSubmit.click();

            const timeout = setTimeout(() => {
                clearInterval(timer);
                reject(new Error("Backend request timed out."));
            }, 26000);

            const timer = setInterval(() => {
                const raw = asText(state.bridgeOutput && state.bridgeOutput.value).trim();
                if (!raw) {
                    return;
                }

                let parsed;
                try {
                    parsed = JSON.parse(raw);
                } catch (_err) {
                    return;
                }

                if (parsed.request_id && parsed.request_id !== requestId) {
                    return;
                }

                clearTimeout(timeout);
                clearInterval(timer);

                if (parsed.ok) {
                    resolve(parsed.data || {});
                } else {
                    reject(new Error(parsed.error || "Request failed."));
                }
            }, 120);
        });
    }

    function callBridge(action, data) {
        const run = () => callBridgeRaw(action, data);
        const chained = state.bridgeQueue.then(run, run);
        state.bridgeQueue = chained.catch(() => null);
        return chained;
    }

    function toast(message, kind) {
        const wrap = byId("rp-toast-wrap");
        if (!wrap) {
            return;
        }
        const t = document.createElement("div");
        t.className = "rp-toast " + (kind === "ok" ? "ok" : "err");
        t.textContent = asText(message || (kind === "ok" ? "Done." : "Something went wrong."));
        wrap.appendChild(t);
        setTimeout(() => {
            if (t.parentNode) {
                t.parentNode.removeChild(t);
            }
        }, 3200);
    }

    function setBusy(show, text) {
        const node = byId("rp-busy");
        const txt = byId("rp-busy-text");
        if (!node || !txt) {
            return;
        }
        txt.textContent = asText(text || "Loading...");
        if (show) {
            node.classList.add("active");
        } else {
            node.classList.remove("active");
        }
    }

    function clearAuthMessage() {
        root.querySelectorAll(".runtime-auth-msg").forEach((node) => {
            if (node && node.parentNode) {
                node.parentNode.removeChild(node);
            }
        });
    }

    function getAuthForm(kind) {
        const action = kind === "register" ? "/register" : "/login";
        return root.querySelector('form[action="' + action + '"]');
    }

    function setAuthMessage(message, isError, kind) {
        clearAuthMessage();
        if (!message) {
            return;
        }

        const targetKind = kind || (isRegister ? "register" : "login");
        const form = getAuthForm(targetKind);
        if (!form) {
            toast(message, isError ? "err" : "ok");
            return;
        }

        const box = document.createElement("div");
        box.className = "form-error runtime-auth-msg";
        box.textContent = asText(message);
        form.insertBefore(box, form.firstChild);
    }

    function updatePill(role) {
      const leftPill = byId("leftPill");
      const pillText = byId("pillText");
      if (!leftPill || !pillText) {
          return;
      }
      leftPill.className = "left-role-pill " + role;
      if (role === "retailer") {
        pillText.textContent = isRegister ? "Registering as Retailer" : "Signing in as Retailer";
      } else {
        pillText.textContent = isRegister ? "Registering as Customer" : "Shopping as Customer";
      }
    }

    function animateTextChange(callback) {
      const textStage = byId("textStage");
      if (!textStage) {
          callback();
          return;
      }
      textStage.classList.add("fade-out");
      setTimeout(() => {
        callback();
        textStage.classList.remove("fade-out");
        textStage.classList.add("fade-in");
        setTimeout(() => textStage.classList.remove("fade-in"), 400);
      }, 300);
    }

    function setMode(register) {
      const slider = byId("slider");
      const tabLogin = byId("tabLogin");
      const tabRegister = byId("tabRegister");
      const leftTitle = byId("leftTitle");
      const leftDesc = byId("leftDesc");

      isRegister = Boolean(register);
      if (slider) {
          slider.style.transform = isRegister ? "translateX(-50%)" : "translateX(0%)";
      }
      if (tabLogin) {
          tabLogin.classList.toggle("active", !isRegister);
      }
      if (tabRegister) {
          tabRegister.classList.toggle("active", isRegister);
      }

      const role = isRegister ? rRole : lRole;
      animateTextChange(() => {
        if (leftTitle && leftDesc) {
            if (isRegister) {
              leftTitle.innerHTML = "Start Your<br>Story.";
              leftDesc.textContent = role === "retailer"
                ? "Open your store and reach customers today."
                : "Discover amazing products from verified retailers.";
            } else {
              leftTitle.innerHTML = "Hello,<br>Welcome!";
              leftDesc.textContent = role === "retailer"
                ? "Sign in to manage your store and orders."
                : "Sign in to continue shopping your favourite items.";
            }
        }
        updatePill(role);
      });
      clearAuthMessage();
    }

    function setLoginRole(role) {
      lRole = role;
      const roleInput = byId("lRoleInput");
      const customerBtn = byId("lBtnCustomer");
      const retailerBtn = byId("lBtnRetailer");
      const loginBtn = byId("loginBtn");

      if (roleInput) {
          roleInput.value = role;
      }
      if (customerBtn) {
          customerBtn.className = "role-sel-btn" + (role === "customer" ? " sel-customer" : "");
      }
      if (retailerBtn) {
          retailerBtn.className = "role-sel-btn" + (role === "retailer" ? " sel-retailer" : "");
      }
      if (loginBtn) {
          loginBtn.className = "primary-btn" + (role === "retailer" ? " retailer-btn" : "");
      }
      if (!isRegister) {
          updatePill(role);
      }
    }

    function setRegRole(role) {
      rRole = role;
      const roleInput = byId("rRoleInput");
      const customerBtn = byId("rBtnCustomer");
      const retailerBtn = byId("rBtnRetailer");
      const registerBtn = byId("registerBtn");

      if (roleInput) {
          roleInput.value = role;
      }
      if (customerBtn) {
          customerBtn.className = "role-sel-btn" + (role === "customer" ? " sel-customer" : "");
      }
      if (retailerBtn) {
          retailerBtn.className = "role-sel-btn" + (role === "retailer" ? " sel-retailer" : "");
      }
      if (registerBtn) {
          registerBtn.className = "primary-btn" + (role === "retailer" ? " retailer-btn" : "");
      }
      if (isRegister) {
          updatePill(role);
      }
    }

    function togglePassword() {
      const input = byId("loginPass");
      const toggle = root.querySelector(".pass-toggle");
      if (!input || !toggle) {
          return;
      }
      if (input.type === "password") {
          input.type = "text";
          toggle.textContent = "HIDE";
      } else {
          input.type = "password";
          toggle.textContent = "SHOW";
      }
    }

        // Expose inline auth handlers immediately so auth buttons work
        // even before async bridge initialization completes.
        window.setLoginRole = setLoginRole;
        window.setRegRole = setRegRole;
        window.togglePassword = togglePassword;

    function showAuthView() {
        const auth = byId("rp-auth-view");
        const app = byId("rp-app-view");
        if (auth) {
            auth.classList.add("active");
        }
        if (app) {
            app.classList.remove("active");
        }
    }

    function showAppView() {
        const auth = byId("rp-auth-view");
        const app = byId("rp-app-view");
        if (auth) {
            auth.classList.remove("active");
        }
        if (app) {
            app.classList.add("active");
        }
    }

    function saveSessionToken(token) {
        state.sessionToken = asText(token || "");
        try {
            if (state.sessionToken) {
                localStorage.setItem(STORAGE_KEY, state.sessionToken);
            } else {
                localStorage.removeItem(STORAGE_KEY);
            }
        } catch (_err) {
            // ignore storage failures
        }
    }

    function readSessionToken() {
        try {
            return asText(localStorage.getItem(STORAGE_KEY) || "");
        } catch (_err) {
            return "";
        }
    }

    function resetLoaded() {
        Object.keys(state.loaded).forEach((k) => {
            state.loaded[k] = false;
        });
    }

    function updateSidebarUser(user) {
        const username = asText(user && user.username ? user.username : "Retailer");
        const email = asText(user && user.email ? user.email : "retailer@shop.com");
        const avatar = username.trim() ? username.trim().charAt(0).toUpperCase() : "R";
        const un = byId("rp-sidebar-user");
        const em = byId("rp-sidebar-email");
        const av = byId("rp-user-avatar");
        if (un) {
            un.textContent = username;
        }
        if (em) {
            em.textContent = email;
        }
        if (av) {
            av.textContent = avatar;
        }
    }

    async function logout(showMsg) {
        const currentToken = asText(state.sessionToken);
        if (currentToken) {
            try {
                await callBridge("logout", { session_token: currentToken });
            } catch (_err) {
                // ignore logout bridge errors
            }
        }
        saveSessionToken("");
        state.user = null;
        state.pendingToken = "";
        resetLoaded();
        showAuthView();
        setMode(false);
        clearAuthMessage();
        if (showMsg) {
            toast("Logged out.", "ok");
        }
    }

    async function withRetailerSession(action, payload) {
        if (!state.sessionToken) {
            throw new Error("Please login first.");
        }
        const body = Object.assign({}, payload || {}, { session_token: state.sessionToken });
        try {
            return await callBridge(action, body);
        } catch (err) {
            const msg = asText(err && err.message ? err.message : err);
            if (/session/i.test(msg)) {
                await logout(false);
                throw new Error("Session expired. Please login again.");
            }
            throw err;
        }
    }

    function setView(viewName) {
        state.currentView = viewName;
        const navs = root.querySelectorAll(".rp-nav-btn[data-nav]");
        navs.forEach((btn) => {
            btn.classList.toggle("active", btn.getAttribute("data-nav") === viewName);
        });

        const views = root.querySelectorAll(".rp-view[data-view]");
        views.forEach((v) => {
            v.classList.toggle("active", v.getAttribute("data-view") === viewName);
        });

        const title = byId("rp-view-title");
        if (title) {
            title.textContent = VIEW_TITLES[viewName] || "Retailer";
        }
    }

    function emptyRow(colspan, text) {
        return '<tr><td colspan="' + colspan + '">' + escapeHtml(text) + "</td></tr>";
    }

    function renderDashboard(data) {
        const stats = (data && data.stats) || {};
        byId("rp-stat-products").textContent = asText(stats.product_count || 0);
        byId("rp-stat-orders").textContent = asText(stats.order_count || 0);
        byId("rp-stat-revenue").textContent = fmtMoney(stats.revenue || 0);
        byId("rp-stat-pending").textContent = asText(stats.pending_count || 0);

        const recentBody = byId("rp-dashboard-orders-body");
        const topBody = byId("rp-dashboard-top-body");
        const recent = (data && data.recent_orders) || [];
        const top = (data && data.top_products) || [];

        if (recentBody) {
            if (!recent.length) {
                recentBody.innerHTML = emptyRow(6, "No retailer orders yet.");
            } else {
                recentBody.innerHTML = recent
                    .map((row) =>
                        '<tr>' +
                        '<td>#' + escapeHtml(row.id) + '</td>' +
                        '<td>' + escapeHtml(row.shipping_name || "-") + '</td>' +
                        '<td>' + escapeHtml(row.status || "-") + '</td>' +
                        '<td>' + escapeHtml(row.items_summary || "-") + '</td>' +
                        '<td>' + escapeHtml(fmtMoney(row.total_amount || 0)) + '</td>' +
                        '<td>' + escapeHtml(asText(row.created_at || "").slice(0, 16)) + '</td>' +
                        '</tr>'
                    )
                    .join("");
            }
        }

        if (topBody) {
            if (!top.length) {
                topBody.innerHTML = emptyRow(4, "No product performance data yet.");
            } else {
                topBody.innerHTML = top
                    .map((row) =>
                        '<tr>' +
                        '<td>' + escapeHtml(row.name || "-") + '</td>' +
                        '<td>' + escapeHtml(row.stock == null ? "-" : row.stock) + '</td>' +
                        '<td>' + escapeHtml(fmtMoney(row.price || 0)) + '</td>' +
                        '<td>' + escapeHtml(row.sold || 0) + '</td>' +
                        '</tr>'
                    )
                    .join("");
            }
        }
    }

    function buildCategoryOptions(categories) {
        const base = '<option value="">No Category</option>';
        const extra = (categories || [])
            .map((c) => '<option value="' + escapeHtml(c.id) + '">' + escapeHtml(c.name) + '</option>')
            .join("");
        return base + extra;
    }

    function renderProducts() {
        const body = byId("rp-products-body");
        const categorySelect = byId("rp-product-category");
        if (categorySelect) {
            categorySelect.innerHTML = buildCategoryOptions(state.categories);
        }

        if (!body) {
            return;
        }

        if (!state.products.length) {
            body.innerHTML = emptyRow(7, "No products found for this retailer.");
            return;
        }

        body.innerHTML = state.products
            .map((p) => {
                const status = Number(p.is_active || 0) === 1 ? "Active" : "Inactive";
                return (
                    '<tr>' +
                    '<td>#' + escapeHtml(p.id) + '</td>' +
                    '<td>' + escapeHtml(p.name || "") + '</td>' +
                    '<td>' + escapeHtml(p.category_name || "-") + '</td>' +
                    '<td>' + escapeHtml(fmtMoney(p.price || 0)) + '</td>' +
                    '<td>' + escapeHtml(p.stock || 0) + '</td>' +
                    '<td>' + escapeHtml(status) + '</td>' +
                    '<td><button class="rp-btn-ghost" data-edit-product="' + escapeHtml(p.id) + '">Edit</button></td>' +
                    '</tr>'
                );
            })
            .join("");
    }

    function fillProductForm(product) {
        byId("rp-product-id").value = product ? asText(product.id || "") : "";
        byId("rp-product-name").value = product ? asText(product.name || "") : "";
        byId("rp-product-description").value = product ? asText(product.description || "") : "";
        byId("rp-product-price").value = product ? asText(product.price || "") : "";
        byId("rp-product-original-price").value = product ? asText(product.original_price || "") : "";
        byId("rp-product-stock").value = product ? asText(product.stock || "") : "";
        byId("rp-product-category").value = product && product.category_id ? asText(product.category_id) : "";
        byId("rp-product-sku").value = product ? asText(product.sku || "") : "";
        byId("rp-product-weight").value = product ? asText(product.weight_grams || "") : "";
        byId("rp-product-image").value = product ? asText(product.image_url || "") : "";
        byId("rp-product-active").value = product ? (Number(product.is_active || 0) === 1 ? "1" : "0") : "1";
    }

    function readProductForm() {
        return {
            product_id: asText(byId("rp-product-id").value || ""),
            name: asText(byId("rp-product-name").value || "").trim(),
            description: asText(byId("rp-product-description").value || ""),
            price: asText(byId("rp-product-price").value || "").trim(),
            original_price: asText(byId("rp-product-original-price").value || "").trim(),
            stock: asText(byId("rp-product-stock").value || "").trim(),
            category_id: asText(byId("rp-product-category").value || "").trim(),
            sku: asText(byId("rp-product-sku").value || ""),
            weight_grams: asText(byId("rp-product-weight").value || "").trim(),
            image_url: asText(byId("rp-product-image").value || ""),
            is_active: asText(byId("rp-product-active").value || "1"),
        };
    }

    function renderOrders() {
        const body = byId("rp-orders-body");
        if (!body) {
            return;
        }

        if (!state.orders.length) {
            body.innerHTML = emptyRow(6, "No orders found for current filter.");
            return;
        }

        body.innerHTML = state.orders
            .map((o) => {
                const options = STATUS_OPTIONS
                    .map((st) => {
                        const sel = st === asText(o.status || "") ? " selected" : "";
                        return '<option value="' + st + '"' + sel + '>' + st + '</option>';
                    })
                    .join("");

                return (
                    '<tr>' +
                    '<td>#' + escapeHtml(o.id) + '<br><small>' + escapeHtml(asText(o.created_at || "").slice(0, 16)) + '</small></td>' +
                    '<td>' + escapeHtml(o.shipping_name || "-") + '<br><small>' + escapeHtml(o.shipping_phone || "") + '</small></td>' +
                    '<td>' + escapeHtml(o.items_summary || "-") + '</td>' +
                    '<td>' + escapeHtml(fmtMoney(o.retailer_total || 0)) + '</td>' +
                    '<td><select class="rp-select" data-order-select="' + escapeHtml(o.id) + '">' + options + '</select></td>' +
                    '<td><button class="rp-btn-ghost" data-save-order="' + escapeHtml(o.id) + '">Save</button></td>' +
                    '</tr>'
                );
            })
            .join("");
    }

    function renderBotPayload(payload) {
        if (!payload || typeof payload !== "object") {
            return escapeHtml(asText(payload || ""));
        }
        const answer = asText(payload.answer || payload.reply || payload.error || "");
        const query = asText(payload.query_ran || "");
        const dbOutput = asText(payload.db_output || "");
        const status = asText(payload.status || "");
        let html = '<div class="rp-msg-section"><div>' + escapeHtml(answer) + '</div></div>';

        if (query) {
            html += '<div class="rp-msg-section"><div class="rp-msg-title">Query</div><div class="rp-msg-pre">' + escapeHtml(query) + '</div></div>';
        }
        if (dbOutput) {
            html += '<div class="rp-msg-section"><div class="rp-msg-title">DB Output</div><div class="rp-msg-pre">' + escapeHtml(dbOutput) + '</div></div>';
        }
        if (status) {
            html += '<div class="rp-msg-section"><div class="rp-msg-title">Status</div><div class="rp-msg-pre">' + escapeHtml(status) + '</div></div>';
        }
        return html;
    }

    function getChatNode() {
        return byId("rp-assistant-messages");
    }

    function appendChatMessage(role, message, createdAt) {
        const chat = getChatNode();
        if (!chat) {
            return;
        }
        const bubble = document.createElement("div");
        bubble.className = "rp-msg " + (role === "user" ? "user" : "bot");

        if (role === "bot") {
            bubble.innerHTML = renderBotPayload(message);
        } else {
            bubble.textContent = asText(message || "");
        }

        const meta = document.createElement("div");
        meta.className = "rp-msg-meta";
        meta.textContent = createdAt ? asText(createdAt) : role === "user" ? "You" : "Sage";
        bubble.appendChild(meta);

        chat.appendChild(bubble);
        chat.scrollTop = chat.scrollHeight;
    }

    function appendTyping() {
        const chat = getChatNode();
        if (!chat) {
            return;
        }
        const bubble = document.createElement("div");
        bubble.className = "rp-msg bot";
        bubble.innerHTML = '<div class="rp-typing"><span></span><span></span><span></span></div>';
        chat.appendChild(bubble);
        chat.scrollTop = chat.scrollHeight;
        state.typingNode = bubble;
    }

    function removeTyping() {
        if (state.typingNode && state.typingNode.parentNode) {
            state.typingNode.parentNode.removeChild(state.typingNode);
        }
        state.typingNode = null;
    }

    async function loadDashboard(force) {
        if (!force && state.loaded.dashboard) {
            return;
        }
        setBusy(true, "Loading dashboard...");
        try {
            const data = await withRetailerSession("retailer_dashboard", {});
            renderDashboard(data);
            state.loaded.dashboard = true;
        } finally {
            setBusy(false);
        }
    }

    async function loadProducts(force) {
        if (!force && state.loaded.products) {
            return;
        }
        setBusy(true, "Loading products...");
        try {
            const data = await withRetailerSession("retailer_products", {});
            state.products = data.products || [];
            state.categories = data.categories || [];
            renderProducts();
            state.loaded.products = true;
        } finally {
            setBusy(false);
        }
    }

    async function loadOrders(force) {
        if (!force && state.loaded.orders) {
            return;
        }
        setBusy(true, "Loading orders...");
        try {
            const status = asText(byId("rp-orders-filter") && byId("rp-orders-filter").value).trim();
            const data = await withRetailerSession("retailer_orders", { status: status });
            state.orders = data.orders || [];
            renderOrders();
            state.loaded.orders = true;
        } finally {
            setBusy(false);
        }
    }

    async function loadProfile(force) {
        if (!force && state.loaded.profile) {
            return;
        }
        setBusy(true, "Loading profile...");
        try {
            const data = await withRetailerSession("retailer_profile", {});
            const user = data.user || {};
            byId("rp-profile-name").value = asText(user.username || "");
            byId("rp-profile-email").value = asText(user.email || "");
            byId("rp-profile-phone").value = asText(user.phone_number || "");
            byId("rp-profile-bio").value = asText(user.bio || "");
            state.loaded.profile = true;
        } finally {
            setBusy(false);
        }
    }

    async function loadAssistantHistory(force) {
        if (!force && state.loaded.assistant) {
            return;
        }
        setBusy(true, "Loading assistant chat...");
        try {
            const data = await withRetailerSession("assistant_history", {});
            const history = data.history || [];
            const chat = getChatNode();
            if (chat) {
                chat.innerHTML = "";
            }
            if (!history.length) {
                appendChatMessage("bot", "Hello, I am Sage. Ask me about products, stock, orders, or growth.", "");
            } else {
                history.forEach((item) => {
                    appendChatMessage(item.sender || "bot", item.message || "", item.created_at || "");
                });
            }
            state.loaded.assistant = true;
        } finally {
            setBusy(false);
        }
    }

    async function loadCurrentView(force) {
        if (state.currentView === "dashboard") {
            await loadDashboard(force);
            return;
        }
        if (state.currentView === "products") {
            await loadProducts(force);
            return;
        }
        if (state.currentView === "orders") {
            await loadOrders(force);
            return;
        }
        if (state.currentView === "assistant") {
            await loadAssistantHistory(force);
            return;
        }
        if (state.currentView === "profile") {
            await loadProfile(force);
        }
    }

    async function switchView(viewName, force) {
        setView(viewName);
        try {
            await loadCurrentView(Boolean(force));
        } catch (err) {
            toast(asText(err && err.message ? err.message : err), "err");
        }
    }

    async function saveProduct() {
        const form = readProductForm();
        if (!form.name) {
            toast("Product name is required.", "err");
            return;
        }

        setBusy(true, "Saving product...");
        try {
            if (form.product_id) {
                await withRetailerSession("retailer_edit_product", form);
                toast("Product updated.", "ok");
            } else {
                await withRetailerSession("retailer_add_product", form);
                toast("Product added.", "ok");
            }
            fillProductForm(null);
            state.loaded.products = false;
            state.loaded.dashboard = false;
            await loadProducts(true);
        } catch (err) {
            toast(asText(err && err.message ? err.message : err), "err");
        } finally {
            setBusy(false);
        }
    }

    async function deactivateCurrentProduct() {
        const id = asText(byId("rp-product-id").value || "").trim();
        if (!id) {
            toast("Select a product first.", "err");
            return;
        }

        setBusy(true, "Deactivating product...");
        try {
            await withRetailerSession("retailer_delete_product", { product_id: id });
            toast("Product deactivated.", "ok");
            fillProductForm(null);
            state.loaded.products = false;
            state.loaded.dashboard = false;
            await loadProducts(true);
        } catch (err) {
            toast(asText(err && err.message ? err.message : err), "err");
        } finally {
            setBusy(false);
        }
    }

    async function saveOrderStatus(orderId) {
        const sel = root.querySelector('[data-order-select="' + orderId + '"]');
        if (!sel) {
            toast("Order status selector missing.", "err");
            return;
        }
        setBusy(true, "Updating order status...");
        try {
            await withRetailerSession("retailer_update_order_status", {
                order_id: orderId,
                status: asText(sel.value || ""),
            });
            toast("Order status updated.", "ok");
            state.loaded.orders = false;
            state.loaded.dashboard = false;
            await loadOrders(true);
        } catch (err) {
            toast(asText(err && err.message ? err.message : err), "err");
        } finally {
            setBusy(false);
        }
    }

    async function saveProfile() {
        setBusy(true, "Updating profile...");
        try {
            const payload = {
                username: asText(byId("rp-profile-name").value || "").trim(),
                phone_number: asText(byId("rp-profile-phone").value || "").trim(),
                bio: asText(byId("rp-profile-bio").value || "").trim(),
            };
            await withRetailerSession("retailer_profile_update", payload);
            if (state.user) {
                state.user.username = payload.username || state.user.username;
                updateSidebarUser(state.user);
            }
            toast("Profile updated.", "ok");
        } catch (err) {
            toast(asText(err && err.message ? err.message : err), "err");
        } finally {
            setBusy(false);
        }
    }

    async function sendAssistantText(messageText) {
        const text = asText(messageText || "").trim();
        if (!text) {
            return;
        }
        appendChatMessage("user", text, "");
        appendTyping();
        try {
            const data = await withRetailerSession("assistant_chat", { message: text });
            removeTyping();
            appendChatMessage("bot", data.reply || "No response.", "");
        } catch (err) {
            removeTyping();
            appendChatMessage("bot", { answer: asText(err && err.message ? err.message : err) }, "");
        }
    }

    async function handleLoginSubmit(event) {
        event.preventDefault();
        clearAuthMessage();
        setBusy(true, "Logging in...");

        try {
            const form = getAuthForm("login");
            if (!form) {
                throw new Error("Login form not found.");
            }
            const emailField = form.querySelector('input[name="email"]');
            const passwordField = form.querySelector('input[name="password"]');
            const email = asText(emailField && emailField.value).trim();
            const password = asText(passwordField && passwordField.value);
            const role = asText(byId("lRoleInput") && byId("lRoleInput").value).trim().toLowerCase() || "customer";

            const data = await callBridge("login", {
                email: email,
                password: password,
                role: role,
            });

            if (asText(data.user && data.user.role).toLowerCase() !== "retailer") {
                saveSessionToken("");
                state.user = null;
                setAuthMessage("Customer login is not available in this retailer portal. Please choose Retailer.", true, "login");
                return;
            }

            state.user = data.user || null;
            saveSessionToken(data.session_token || "");
            updateSidebarUser(state.user || {});
            showAppView();
            setView("dashboard");
            resetLoaded();
            await loadCurrentView(true);
            toast("Welcome back.", "ok");
        } catch (err) {
            setAuthMessage(asText(err && err.message ? err.message : err), true, "login");
        } finally {
            setBusy(false);
        }
    }

    async function handleRegisterSubmit(event) {
        event.preventDefault();
        clearAuthMessage();
        setBusy(true, "Creating account...");

        try {
            const form = getAuthForm("register");
            if (!form) {
                throw new Error("Register form not found.");
            }
            const usernameField = form.querySelector('input[name="username"]');
            const emailField = form.querySelector('input[name="email"]');
            const passwordField = form.querySelector('input[name="password"]');
            const role = asText(byId("rRoleInput") && byId("rRoleInput").value).trim().toLowerCase() || "customer";

            const payload = {
                username: asText(usernameField && usernameField.value).trim(),
                phone: "",
                email: asText(emailField && emailField.value).trim(),
                password: asText(passwordField && passwordField.value),
                role: role,
            };

            const registration = await callBridge("register", payload);
            state.pendingToken = asText(registration.pending_token || "");

            const verification = await callBridge("verify_otp", {
                pending_token: state.pendingToken,
                otp: asText(registration.otp_hint || ""),
            });

            if (asText(verification.user && verification.user.role).toLowerCase() !== "retailer") {
                saveSessionToken("");
                state.user = null;
                setMode(false);
                setAuthMessage("Customer account created. Please choose Retailer to access this portal.", true, "login");
                toast("Account created successfully.", "ok");
                return;
            }

            state.user = verification.user || null;
            saveSessionToken(verification.session_token || "");
            updateSidebarUser(state.user || {});
            showAppView();
            setView("dashboard");
            resetLoaded();
            await loadCurrentView(true);
            toast("Account verified successfully.", "ok");
        } catch (err) {
            setAuthMessage(asText(err && err.message ? err.message : err), true, "register");
        } finally {
            setBusy(false);
        }
    }

    async function trySessionResume() {
        const token = readSessionToken();
        if (!token) {
            showAuthView();
            return;
        }

        saveSessionToken(token);
        setBusy(true, "Restoring session...");
        try {
            const data = await callBridge("session_resume", { session_token: token });
            if (asText(data.user && data.user.role).toLowerCase() !== "retailer") {
                saveSessionToken("");
                state.user = null;
                showAuthView();
                return;
            }
            state.user = data.user || null;
            updateSidebarUser(state.user || {});
            showAppView();
            setView("dashboard");
            resetLoaded();
            await loadCurrentView(true);
        } catch (_err) {
            saveSessionToken("");
            showAuthView();
        } finally {
            setBusy(false);
        }
    }

    function bindEvents() {
        const tabLogin = byId("tabLogin");
        const tabRegister = byId("tabRegister");
        const toggleBtn = byId("toggleBtn");
        const loginForm = getAuthForm("login");
        const registerForm = getAuthForm("register");

        if (tabLogin) {
            tabLogin.onclick = () => setMode(false);
        }
        if (tabRegister) {
            tabRegister.onclick = () => setMode(true);
        }
        if (toggleBtn) {
            toggleBtn.onclick = () => {
                window.location.href = "/landing";
            };
        }

        const lBtnCustomer = byId("lBtnCustomer");
        if (lBtnCustomer) lBtnCustomer.onclick = () => setLoginRole("customer");
        const lBtnRetailer = byId("lBtnRetailer");
        if (lBtnRetailer) lBtnRetailer.onclick = () => setLoginRole("retailer");
        
        const rBtnCustomer = byId("rBtnCustomer");
        if (rBtnCustomer) rBtnCustomer.onclick = () => setRegRole("customer");
        const rBtnRetailer = byId("rBtnRetailer");
        if (rBtnRetailer) rBtnRetailer.onclick = () => setRegRole("retailer");

        root.querySelectorAll(".pass-toggle").forEach(pt => {
            pt.onclick = () => togglePassword();
        });

        if (loginForm) {
            loginForm.addEventListener("submit", handleLoginSubmit);
        }
        if (registerForm) {
            registerForm.addEventListener("submit", handleRegisterSubmit);
        }

        const logoutBtn = byId("rp-logout-btn");
        if (logoutBtn) {
            logoutBtn.addEventListener("click", async () => {
                await logout(true);
            });
        }

        root.querySelectorAll(".rp-nav-btn[data-nav]").forEach((btn) => {
            btn.addEventListener("click", async () => {
                const target = btn.getAttribute("data-nav");
                await switchView(target, false);
            });
        });

        const refreshBtn = byId("rp-top-refresh");
        if (refreshBtn) {
            refreshBtn.addEventListener("click", async () => {
                state.loaded[state.currentView] = false;
                await loadCurrentView(true);
                toast("View refreshed.", "ok");
            });
        }

        const productsBody = byId("rp-products-body");
        if (productsBody) {
            productsBody.addEventListener("click", (event) => {
                const btn = event.target.closest("[data-edit-product]");
                if (!btn) {
                    return;
                }
                const id = asText(btn.getAttribute("data-edit-product") || "");
                const product = state.products.find((p) => asText(p.id) === id);
                if (product) {
                    fillProductForm(product);
                    toast("Editing product #" + id, "ok");
                }
            });
        }

        const saveProductBtn = byId("rp-product-save");
        const clearProductBtn = byId("rp-product-clear");
        const deleteProductBtn = byId("rp-product-delete");

        if (saveProductBtn) {
            saveProductBtn.addEventListener("click", saveProduct);
        }
        if (clearProductBtn) {
            clearProductBtn.addEventListener("click", () => fillProductForm(null));
        }
        if (deleteProductBtn) {
            deleteProductBtn.addEventListener("click", deactivateCurrentProduct);
        }

        const ordersBody = byId("rp-orders-body");
        if (ordersBody) {
            ordersBody.addEventListener("click", async (event) => {
                const btn = event.target.closest("[data-save-order]");
                if (!btn) {
                    return;
                }
                const id = asText(btn.getAttribute("data-save-order") || "");
                await saveOrderStatus(id);
            });
        }

        const ordersRefresh = byId("rp-orders-refresh");
        const ordersFilter = byId("rp-orders-filter");
        if (ordersRefresh) {
            ordersRefresh.addEventListener("click", async () => {
                state.loaded.orders = false;
                await loadOrders(true);
            });
        }
        if (ordersFilter) {
            ordersFilter.addEventListener("change", async () => {
                state.loaded.orders = false;
                await loadOrders(true);
            });
        }

        const profileSave = byId("rp-profile-save");
        if (profileSave) {
            profileSave.addEventListener("click", saveProfile);
        }

        const assistantSend = byId("rp-assistant-send");
        const assistantInput = byId("rp-assistant-input");
        if (assistantSend && assistantInput) {
            assistantSend.addEventListener("click", async () => {
                const text = asText(assistantInput.value || "").trim();
                if (!text) {
                    return;
                }
                assistantInput.value = "";
                await sendAssistantText(text);
            });

            assistantInput.addEventListener("keydown", async (event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    const text = asText(assistantInput.value || "").trim();
                    if (!text) {
                        return;
                    }
                    assistantInput.value = "";
                    await sendAssistantText(text);
                }
            });
        }

        root.querySelectorAll(".rp-chip[data-prompt]").forEach((chip) => {
            chip.addEventListener("click", async () => {
                const prompt = asText(chip.getAttribute("data-prompt") || "").trim();
                if (!prompt) {
                    return;
                }
                setView("assistant");
                await loadAssistantHistory(false);
                await sendAssistantText(prompt);
            });
        });
    }

    async function bootstrap() {
        root = document.getElementById("rp-root");
        if (!root) {
            return;
        }
        if (root.dataset.bound === "1") {
            return;
        }
        root.dataset.bound = "1";

        bindEvents();
        setMode(false);
        setLoginRole("customer");
        setRegRole("customer");
        fillProductForm(null);

        try {
            await waitForBridge();
            await trySessionResume();
        } catch (err) {
            setAuthMessage(asText(err && err.message ? err.message : err), true, "login");
            showAuthView();
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", bootstrap);
    } else {
        bootstrap();
    }

    if (!root) {
        let tries = 0;
        const waitForRoot = () => {
            if (document.getElementById("rp-root")) {
                bootstrap();
                return;
            }
            tries += 1;
            if (tries < 120) {
                setTimeout(waitForRoot, 50);
            }
        };
        waitForRoot();
    }
}
"""


# ============================================================
# [SECTION: GRADIO APP]
# ============================================================
def build_app(embed_assets_in_constructor: bool = False) -> Any:
    if gr is None:
        raise RuntimeError("Gradio is not installed. Install it with: pip install gradio")

    blocks_kwargs: dict[str, Any] = {"title": "Shopy Retailer Portal"}
    if embed_assets_in_constructor:
        blocks_kwargs["css"] = APP_CSS
        blocks_kwargs["js"] = APP_JS

    with gr.Blocks(**blocks_kwargs) as demo:
        with gr.Column(elem_id="bridge-zone"):
            bridge_action = gr.Textbox(value="", label="action", elem_id="bridge-action")
            bridge_payload_in = gr.Textbox(value="", label="payload_in", elem_id="bridge-payload-in")
            bridge_output = gr.Textbox(value="", label="output", elem_id="bridge-output")
            bridge_submit = gr.Button("submit", elem_id="bridge-submit")

        gr.HTML(RETAILER_UI_HTML)

        bridge_submit.click(
            fn=bridge_dispatch,
            inputs=[bridge_action, bridge_payload_in],
            outputs=[bridge_output],
            queue=False,
        )

    return demo


def main() -> None:
    if gr is None:
        raise RuntimeError("Gradio is not installed. Install it with: pip install gradio")

    bootstrap_database(DB_PATH)
    in_colab = ("google.colab" in sys.modules) or bool(os.getenv("COLAB_RELEASE_TAG"))

    launch_sig = inspect.signature(gr.Blocks.launch)
    launch_supports_assets = ("css" in launch_sig.parameters) and ("js" in launch_sig.parameters)

    app = build_app(embed_assets_in_constructor=not launch_supports_assets)

    launch_kwargs: dict[str, Any] = {
        "share": in_colab,
        "show_error": False,
    }
    if launch_supports_assets:
        launch_kwargs["css"] = APP_CSS
        launch_kwargs["js"] = APP_JS

    app.launch(**launch_kwargs)


if __name__ == "__main__":
    main()
