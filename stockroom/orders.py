from flask import current_app
from itsdangerous import SignatureExpired, URLSafeTimedSerializer


QUOTE_LIFETIME = 900
PRODUCT_QUOTE = "stockroom-checkout-v1"
CART_QUOTE = "stockroom-cart-v1"


class StockConflict(ValueError):
    pass


def quote_serializer(salt):
    return URLSafeTimedSerializer(current_app.secret_key, salt=salt)


def read_quote(token, salt):
    serializer = quote_serializer(salt)
    try:
        return serializer.loads(token, max_age=QUOTE_LIFETIME), False
    except SignatureExpired:
        return serializer.loads(token), True


def order_items(db, order_id):
    return db.execute(
        "SELECT * FROM order_items WHERE order_id = ? ORDER BY product_id",
        (order_id,),
    ).fetchall()


def place_order(db, order_id, selections):
    db.execute("INSERT INTO orders (id) VALUES (?)", (order_id,))
    for product, quantity in selections:
        result = db.execute(
            "UPDATE products SET stock = stock - ?, version = version + 1 WHERE id = ? AND stock >= ?",
            (quantity, product["id"], quantity),
        )
        if not result.rowcount:
            raise StockConflict(f"{product['name']} has only {product['stock']:,} units available.")
        db.execute(
            """
            INSERT INTO order_items (order_id, product_id, product_name, sku, unit_price_cents, quantity)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (order_id, product["id"], product["name"], product["sku"], product["price_cents"], quantity),
        )
