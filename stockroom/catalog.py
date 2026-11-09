import csv
import io
import re
from dataclasses import dataclass, field
from decimal import Decimal

from .db import write_transaction


PRODUCT_FIELDS = ("name", "sku", "description", "category", "price", "stock", "weight_kg")
INSERT_PRODUCT = """
    INSERT INTO products (name, sku, description, category, price_cents, stock, weight_grams)
    VALUES (?, ?, ?, ?, ?, ?, ?)
"""
PAGE_SIZE = 20


class ValidationError(ValueError):
    pass


def whole_number(value, label, minimum, maximum):
    if not re.fullmatch(r"[0-9]{1,12}", value):
        raise ValidationError(f"{label} must be a whole number.")
    number = int(value)
    if not minimum <= number <= maximum:
        raise ValidationError(f"{label} must be between {minimum:,} and {maximum:,}.")
    return number


def scaled_decimal(value, label, places, maximum):
    if not re.fullmatch(rf"[0-9]{{1,12}}(?:\.[0-9]{{1,{places}}})?", value):
        raise ValidationError(f"{label} must be nonnegative with up to {places} decimal places.")
    amount = int(Decimal(value) * 10**places)
    if amount > maximum:
        raise ValidationError(f"{label} exceeds the maximum of {Decimal(maximum).scaleb(-places):,}.")
    return amount


def format_weight(grams):
    if grams is None:
        return ""
    kilograms, remainder = divmod(grams, 1000)
    return f"{kilograms}.{remainder:03d}".rstrip("0").rstrip(".")


def product_values(data, *, from_csv=False):
    name, sku, description, category, price, stock, weight = (
        data.get(key, "").strip() for key in PRODUCT_FIELDS
    )
    if not 1 <= len(name) <= 120 or "\x00" in name:
        raise ValidationError("Name must contain 1 to 120 characters and no null bytes.")
    if re.search(r"</?[A-Za-z][^>]*>", name):
        raise ValidationError("Product names must be plain text without HTML tags.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,39}", sku):
        raise ValidationError("SKU must start with a letter or digit and use up to 40 letters, digits, dots, underscores, or hyphens.")
    if len(description) > 2000 or "\x00" in description:
        raise ValidationError("Description must contain at most 2,000 characters and no null bytes.")
    if len(category) > 80 or "\x00" in category:
        raise ValidationError("Category must contain at most 80 characters and no null bytes.")
    if from_csv:
        price = "0" if price.casefold() == "free" else price.removeprefix("$").strip()
    return (
        name,
        sku.upper(),
        description,
        category or "Uncategorized",
        scaled_decimal(price, "Price", 2, 99_999_999),
        whole_number(stock, "Stock", 0, 999_999),
        scaled_decimal(weight, "Weight", 3, 999_999_999) if weight else None,
    )


@dataclass
class ImportReport:
    inserted: int = 0
    duplicates: int = 0
    blank: int = 0
    invalid: int = 0
    issues: list[tuple[int, str]] = field(default_factory=list)


def import_products(db, raw):
    report = ImportReport()
    rows = []
    if len(raw) > 1_000_000:
        raise ValidationError("The CSV must be 1 MB or smaller.")
    try:
        reader = csv.reader(io.StringIO(raw.decode("utf-8-sig"), newline=""), strict=True)
        header = tuple(column.strip().lower() for column in next(reader, ()))
        if len(header) != len(PRODUCT_FIELDS) or set(header) != set(PRODUCT_FIELDS):
            raise ValidationError("The CSV must have one header for each required column.")
        for index in range(1, 5002):
            line = reader.line_num + 1
            row = next(reader, None)
            if row is None:
                break
            if index > 5000:
                raise ValidationError("The CSV can contain up to 5,000 data rows.")
            if not row or (len(row) == len(header) and not any(cell.strip() for cell in row)):
                report.blank += 1
                continue
            try:
                if len(row) != len(header):
                    raise ValidationError(f"Expected {len(header)} columns, found {len(row)}.")
                rows.append((line, product_values(dict(zip(header, row)), from_csv=True)))
            except ValidationError as error:
                report.invalid += 1
                report.issues.append((line, str(error)))
    except UnicodeDecodeError as error:
        raise ValidationError("The CSV must be encoded as UTF-8.") from error
    except csv.Error as error:
        raise ValidationError(f"Malformed CSV near line {reader.line_num}. No rows were imported.") from error

    with write_transaction(db):
        for line, values in rows:
            result = db.execute(INSERT_PRODUCT + " ON CONFLICT(sku) DO NOTHING", values)
            if result.rowcount:
                report.inserted += 1
            else:
                report.duplicates += 1
                report.issues.append((line, f"SKU {values[1]} already exists."))
    report.issues.sort()
    return report


def search_products(db, query, category, page):
    search = f"%{query.replace('!', '!!').replace('%', '!%').replace('_', '!_')}%"
    products = db.execute(
        """
        SELECT * FROM products
        WHERE (? = '' OR category = ?)
          AND (? = '' OR name LIKE ? ESCAPE '!' OR sku LIKE ? ESCAPE '!'
               OR description LIKE ? ESCAPE '!' OR category LIKE ? ESCAPE '!')
        ORDER BY name COLLATE NOCASE, id
        LIMIT ? OFFSET ?
        """,
        (category, category, query, search, search, search, search, PAGE_SIZE + 1, (page - 1) * PAGE_SIZE),
    ).fetchall()
    return products[:PAGE_SIZE], len(products) > PAGE_SIZE
