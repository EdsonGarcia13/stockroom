import secrets
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from flask import current_app, g


def get_db():
    if "db" not in g:
        db = sqlite3.connect(current_app.config["DATABASE"], isolation_level=None, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        g.db = db
    return g.db


def close_db(error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    db.execute("PRAGMA journal_mode = WAL")
    db.executescript(Path(__file__).with_name("schema.sql").read_text(encoding="utf-8"))
    with write_transaction(db):
        columns = {column["name"] for column in db.execute("PRAGMA table_info(orders)")}
        if "product_id" in columns:
            db.execute("""
                INSERT INTO order_items (order_id, product_id, product_name, sku, unit_price_cents, quantity)
                SELECT id, product_id, product_name, sku, unit_price_cents, quantity FROM orders
            """)
            for column in ("product_id", "product_name", "sku", "unit_price_cents", "quantity"):
                db.execute(f"ALTER TABLE orders DROP COLUMN {column}")


def signing_secret():
    db = get_db()
    query = "SELECT value FROM app_settings WHERE name = 'signing_secret'"
    saved = db.execute(query).fetchone()
    if saved is None:
        db.execute(
            "INSERT INTO app_settings (name, value) VALUES ('signing_secret', ?) ON CONFLICT(name) DO NOTHING",
            (secrets.token_hex(32),),
        )
        saved = db.execute(query).fetchone()
    return saved["value"]


@contextmanager
def write_transaction(db):
    db.execute("BEGIN IMMEDIATE")
    try:
        yield
        db.commit()
    except BaseException:
        db.rollback()
        raise
