CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
    sku TEXT NOT NULL COLLATE NOCASE UNIQUE CHECK (length(sku) BETWEEN 1 AND 40),
    description TEXT NOT NULL CHECK (length(description) <= 2000),
    category TEXT NOT NULL CHECK (length(category) BETWEEN 1 AND 80),
    price_cents INTEGER NOT NULL CHECK (price_cents BETWEEN 0 AND 99999999),
    stock INTEGER NOT NULL CHECK (stock BETWEEN 0 AND 999999),
    weight_grams INTEGER CHECK (weight_grams BETWEEN 0 AND 999999999),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0)
) STRICT;

CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    product_id INTEGER NOT NULL,
    product_name TEXT NOT NULL,
    sku TEXT NOT NULL,
    unit_price_cents INTEGER NOT NULL CHECK (unit_price_cents >= 0),
    quantity INTEGER NOT NULL CHECK (quantity BETWEEN 1 AND 999999),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
) STRICT;

CREATE TABLE IF NOT EXISTS app_settings (
    name TEXT PRIMARY KEY,
    value TEXT NOT NULL
) STRICT;
