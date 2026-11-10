import hashlib
import secrets

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from itsdangerous import BadSignature

from .catalog import ValidationError, whole_number
from .db import get_db, write_transaction
from .orders import CART_QUOTE, StockConflict, order_items, place_order, quote_serializer, read_quote
from .views import verify_csrf


basket = Blueprint("basket", __name__)
basket.before_request(verify_csrf)
MAX_LINES = 50


def cart_owner():
    token = session.get("csrf")
    return hashlib.sha256(token.encode()).hexdigest() if token else None


def cart_count():
    owner = cart_owner()
    if owner is None:
        return 0
    return get_db().execute(
        "SELECT coalesce(sum(quantity), 0) FROM cart_items WHERE cart_id = ?", (owner,),
    ).fetchone()[0]


@basket.app_context_processor
def inject_cart_count():
    return {"cart_count": cart_count}


def cart_lines(db, owner):
    return db.execute(
        """
        SELECT p.id, p.name, p.sku, p.category, p.stock, p.price_cents, i.quantity, c.checkout_id
        FROM cart_items AS i
        JOIN carts AS c ON c.id = i.cart_id
        JOIN products AS p ON p.id = i.product_id
        WHERE i.cart_id = ? ORDER BY p.id
        """,
        (owner,),
    ).fetchall()


def cart_snapshot(lines):
    return [[line["id"], line["quantity"], line["price_cents"]] for line in lines]


def render_cart(error=None, status=200):
    owner = cart_owner()
    lines = cart_lines(get_db(), owner)
    available = all(line["quantity"] <= line["stock"] for line in lines)
    quote = None
    if lines and available:
        quote = quote_serializer(CART_QUOTE).dumps({
            "id": lines[0]["checkout_id"], "owner": owner, "lines": cart_snapshot(lines),
        })
    total = sum(line["price_cents"] * line["quantity"] for line in lines)
    return render_template("cart.html", lines=lines, total=total, quote=quote, error=error), status


@basket.get("/cart")
def cart():
    return render_cart()


def save_quantity(db, owner, product_id, action, quantity):
    existing = db.execute(
        "SELECT quantity FROM cart_items WHERE cart_id = ? AND product_id = ?", (owner, product_id),
    ).fetchone()
    if action == "remove":
        if existing is None:
            return
        db.execute("DELETE FROM cart_items WHERE cart_id = ? AND product_id = ?", (owner, product_id))
        remaining = db.execute("SELECT 1 FROM cart_items WHERE cart_id = ? LIMIT 1", (owner,)).fetchone()
        if remaining is None:
            db.execute("DELETE FROM carts WHERE id = ?", (owner,))
            return
    else:
        product = db.execute("SELECT name, stock FROM products WHERE id = ?", (product_id,)).fetchone()
        if product is None:
            raise ValidationError("This product is no longer available.")
        if action == "update" and existing is None:
            raise ValidationError("This item is no longer in your cart.")
        if action == "add" and existing is not None:
            quantity += existing["quantity"]
        if quantity > product["stock"]:
            raise ValidationError(f"{product['name']} has only {product['stock']:,} units available.")
        if existing is None:
            count = db.execute("SELECT count(*) FROM cart_items WHERE cart_id = ?", (owner,)).fetchone()[0]
            if count >= MAX_LINES:
                raise ValidationError(f"A cart can hold up to {MAX_LINES} different products.")
        elif quantity == existing["quantity"]:
            return

    db.execute(
        """
        INSERT INTO carts (id, checkout_id) VALUES (?, ?)
        ON CONFLICT(id) DO UPDATE SET checkout_id = excluded.checkout_id
        """,
        (owner, secrets.token_urlsafe(24)),
    )
    if action != "remove":
        db.execute(
            """
            INSERT INTO cart_items (cart_id, product_id, quantity) VALUES (?, ?, ?)
            ON CONFLICT(cart_id, product_id) DO UPDATE SET quantity = excluded.quantity
            """,
            (owner, product_id, quantity),
        )


@basket.post("/cart/items/<int:product_id>")
def change_item(product_id):
    action = request.form.get("action")
    if action not in ("add", "update", "remove"):
        abort(400)
    db = get_db()
    try:
        quantity = 0 if action == "remove" else whole_number(
            request.form.get("quantity", "1" if action == "add" else ""), "Quantity", 1, 999_999,
        )
        with write_transaction(db):
            save_quantity(db, cart_owner(), product_id, action, quantity)
    except ValidationError as error:
        flash(str(error), "error")
    else:
        flash({"add": "Added to your cart.", "update": "Cart updated.", "remove": "Item removed."}[action])

    if action == "add":
        source = request.args.get("source")
        if source == "product":
            return redirect(url_for("web.checkout", product_id=product_id), 303)
        if source == "catalog":
            return redirect(url_for(
                "web.catalog", q=request.args.get("q", "")[:120], category=request.args.get("category", "")[:80],
                page=request.args.get("page", "1"),
            ), 303)
    return redirect(url_for("basket.cart"), 303)


@basket.post("/cart/checkout")
def checkout():
    owner = cart_owner()
    try:
        quote, expired = read_quote(request.form.get("quote", ""), CART_QUOTE)
    except BadSignature:
        return render_cart("This checkout is invalid. Review your cart and try again.", 409)
    if quote["owner"] != owner:
        return render_cart("Review your cart before placing an order.", 409)

    db = get_db()
    try:
        with write_transaction(db):
            items = order_items(db, quote["id"])
            if items:
                ordered = [[item["product_id"], item["quantity"], item["unit_price_cents"]] for item in items]
                if ordered != quote["lines"]:
                    return render_template(
                        "error.html", title="Order already placed",
                        message="This cart was already checked out with different prices or quantities.",
                    ), 409
                return redirect(url_for("web.receipt", order_id=quote["id"]), 303)
            if expired:
                return render_cart("This checkout expired. Review your cart before placing the order.", 409)
            lines = cart_lines(db, owner)
            if not lines or lines[0]["checkout_id"] != quote["id"] or cart_snapshot(lines) != quote["lines"]:
                return render_cart("Your cart or its prices changed. Review the updated items and total.", 409)
            place_order(db, quote["id"], ((line, line["quantity"]) for line in lines))
            db.execute("DELETE FROM carts WHERE id = ?", (owner,))
    except StockConflict as error:
        return render_cart(str(error), 409)
    return redirect(url_for("web.receipt", order_id=quote["id"]), 303)
