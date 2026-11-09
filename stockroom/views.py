import secrets
import sqlite3

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for
from itsdangerous import BadSignature, URLSafeTimedSerializer

from .catalog import (
    INSERT_PRODUCT, ValidationError, format_weight, import_products, product_values, search_products, whole_number,
)
from .db import get_db, write_transaction


web = Blueprint("web", __name__)
QUOTE_LIFETIME = 900


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


@web.app_context_processor
def inject_csrf():
    return {"csrf_token": csrf_token}


@web.before_request
def verify_csrf():
    if request.method == "POST":
        expected = session.get("csrf")
        provided = request.form.get("csrf", "")
        if not expected or not secrets.compare_digest(expected.encode(), provided.encode()):
            abort(400)


def get_product(product_id):
    product = get_db().execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    if product is None:
        abort(404)
    return product


def product_form_values(product):
    return {
        "name": product["name"],
        "sku": product["sku"],
        "description": product["description"],
        "category": product["category"],
        "price": f'{product["price_cents"] // 100}.{product["price_cents"] % 100:02d}',
        "stock": product["stock"],
        "weight_kg": format_weight(product["weight_grams"]),
    }


@web.get("/healthz")
def health():
    get_db().execute("SELECT id FROM products LIMIT 1").fetchone()
    return {"status": "ok"}


@web.get("/")
@web.get("/manage", endpoint="manage")
def catalog():
    query = request.args.get("q", "").strip()[:120]
    category = request.args.get("category", "")[:80]
    page_text = request.args.get("page", "1")
    try:
        page = whole_number(page_text, "Page", 1, 1_000_000)
    except ValidationError:
        abort(400)
    db = get_db()
    products, has_next = search_products(db, query, category, page)
    categories = db.execute("SELECT DISTINCT category FROM products ORDER BY category COLLATE NOCASE").fetchall()
    manage = request.endpoint == "web.manage"
    return render_template(
        "catalog.html", products=products, categories=categories, q=query, category=category,
        page=page, has_next=has_next, manage=manage,
    )


@web.route("/manage/products/new", methods=["GET", "POST"])
@web.route("/manage/products/<int:product_id>/edit", methods=["GET", "POST"])
def edit_product(product_id=None):
    product = get_product(product_id) if product_id is not None else None
    values = product_form_values(product) if product is not None else {}
    version = product["version"] if product is not None else None
    if request.method == "GET":
        return render_template("product_form.html", product=product, values=values, version=version)
    values = request.form
    version = values.get("version", "")
    try:
        fields = product_values(values)
        if product is None:
            get_db().execute(INSERT_PRODUCT, fields)
        else:
            row_version = whole_number(version, "Version", 1, 2_147_483_647)
            result = get_db().execute(
                """
                UPDATE products SET name = ?, sku = ?, description = ?, category = ?, price_cents = ?,
                    stock = ?, weight_grams = ?, version = version + 1
                WHERE id = ? AND version = ?
                """,
                (*fields, product_id, row_version),
            )
            if not result.rowcount:
                return render_template("error.html", title="Product changed", message=(
                    "Another action changed this product. Reopen it to review the current values."
                )), 409
    except ValidationError as error:
        return render_template("product_form.html", product=product, values=values, version=version, error=str(error)), 422
    except sqlite3.IntegrityError as error:
        if error.sqlite_errorcode != sqlite3.SQLITE_CONSTRAINT_UNIQUE:
            raise
        return render_template(
            "product_form.html", product=product, values=values, version=version, error="This SKU already exists."
        ), 409
    flash("Product created." if product is None else "Product updated.")
    return redirect(url_for("web.manage"), 303)


@web.post("/manage/products/<int:product_id>/delete")
def delete_product(product_id):
    try:
        version = whole_number(request.form.get("version", ""), "Version", 1, 2_147_483_647)
    except ValidationError:
        abort(400)
    deleted = get_db().execute("DELETE FROM products WHERE id = ? AND version = ?", (product_id, version))
    if not deleted.rowcount:
        return render_template("error.html", title="Product changed", message=(
            "The product changed or was already removed. Reopen the catalog to review its current state."
        )), 409
    flash("Product deleted.")
    return redirect(url_for("web.manage"), 303)


@web.route("/manage/import", methods=["GET", "POST"])
def upload():
    if request.method == "GET":
        return render_template("import.html")
    file = request.files.get("csv")
    if not file or not file.filename:
        return render_template("import.html", error="Choose a CSV file."), 422
    try:
        report = import_products(get_db(), file.stream.read(1_000_001))
    except ValidationError as error:
        return render_template("import.html", error=str(error)), 422
    return render_template("import.html", report=report)


def quote_serializer():
    return URLSafeTimedSerializer(current_app.secret_key, salt="stockroom-checkout-v1")


def checkout_page(product, *, error=None, quantity="1", status=200):
    quote = quote_serializer().dumps({
        "id": secrets.token_urlsafe(24),
        "product": product["id"],
        "price": product["price_cents"],
    })
    return render_template("checkout.html", product=product, quote=quote, error=error, quantity=quantity), status


@web.route("/products/<int:product_id>", methods=["GET", "POST"])
def checkout(product_id):
    if request.method == "GET":
        return checkout_page(get_product(product_id))
    quantity_text = request.form.get("quantity", "")
    try:
        quantity = whole_number(quantity_text, "Quantity", 1, 999_999)
        quote = quote_serializer().loads(request.form.get("quote", ""), max_age=QUOTE_LIFETIME)
        if quote["product"] != product_id:
            raise ValidationError("Review this product before placing an order.")
    except BadSignature:
        return checkout_page(get_product(product_id), error="This checkout expired. Review the current price and try again.", status=409)
    except ValidationError as error:
        return checkout_page(get_product(product_id), error=str(error), quantity=quantity_text, status=422)

    db = get_db()
    conflict = None
    with write_transaction(db):
        order = db.execute("SELECT id, quantity FROM orders WHERE id = ?", (quote["id"],)).fetchone()
        if order is not None:
            if order["quantity"] != quantity:
                return render_template("error.html", title="Order already placed", message=(
                    "Start a new checkout to place an order with a different quantity."
                )), 409
            return redirect(url_for("web.receipt", order_id=order["id"]), 303)
        product = get_product(product_id)
        if product["price_cents"] != quote["price"]:
            conflict = "The price changed. Review the current price before placing the order."
        else:
            result = db.execute(
                "UPDATE products SET stock = stock - ?, version = version + 1 WHERE id = ? AND stock >= ?",
                (quantity, product_id, quantity),
            )
            if not result.rowcount:
                conflict = f"Only {product['stock']:,} units are available. Choose a smaller quantity."
            else:
                db.execute(
                    """
                    INSERT INTO orders (id, product_id, product_name, sku, unit_price_cents, quantity)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (quote["id"], product_id, product["name"], product["sku"], product["price_cents"], quantity),
                )
    if conflict:
        return checkout_page(product, error=conflict, quantity=quantity_text, status=409)
    return redirect(url_for("web.receipt", order_id=quote["id"]), 303)


@web.get("/orders/<order_id>")
def receipt(order_id):
    order = get_db().execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if order is None:
        abort(404)
    return render_template("receipt.html", order=order)
