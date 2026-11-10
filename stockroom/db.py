import secrets
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from flask import current_app, g


def get_db():
    if "db" not in g:
        db = sqlite3.connect(current_app.config["DATABASE"], isolation_level=None, timeout=5)
        db.row_factory = sqlite3.Row
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
