import io
import re
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from itsdangerous import TimestampSigner

from stockroom import create_app
from stockroom.db import get_db


FIXTURE = Path(__file__).resolve().parents[1] / "data" / "challenge-products.csv"
HEADER = b"name,sku,description,category,price,stock,weight_kg\n"


class StockroomTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.app = create_app({
            "TESTING": True,
            "DATABASE": str(Path(self.folder.name) / "test.sqlite3"),
            "SECRET_KEY": "test-only",
        })
        self.client = self.app.test_client()
        self.fields = {
            "name": "Trail Mug", "sku": "m-1", "description": "Ceramic mug", "category": "Kitchen",
            "price": "12.99", "stock": "2", "weight_kg": "0.450",
        }

    def token(self, response, name):
        return re.search(rf'name="{name}" value="([^"]+)"', response.get_data(as_text=True))[1]

    def add_product(self, **overrides):
        csrf = self.token(self.client.get("/manage/products/new"), "csrf")
        response = self.client.post("/manage/products/new", data={**self.fields, **overrides, "csrf": csrf})
        self.assertEqual(response.status_code, 303)
        with self.app.app_context():
            return get_db().execute("SELECT max(id) FROM products").fetchone()[0]

    def upload(self, content):
        csrf = self.token(self.client.get("/manage/import"), "csrf")
        return self.client.post("/manage/import", data={"csrf": csrf, "csv": (io.BytesIO(content), "items.csv")})

    def checkout_form(self, client, product_id, quantity="1"):
        page = client.get(f"/products/{product_id}")
        return {"csrf": self.token(page, "csrf"), "quote": self.token(page, "quote"), "quantity": quantity}

    def change_cart(self, product_id, action="add", quantity="1"):
        csrf = self.token(self.client.get("/manage/products/new"), "csrf")
        response = self.client.post(
            f"/cart/items/{product_id}", data={"csrf": csrf, "action": action, "quantity": quantity},
        )
        self.assertEqual(response.status_code, 303)
        return csrf

    def cart_form(self, csrf):
        return {"csrf": csrf, "quote": self.token(self.client.get("/cart"), "quote")}

    def test_crud_validation_search_and_delete(self):
        product_id = self.add_product()
        self.assertIn(b"Trail Mug", self.client.get("/?q=m-1&category=Kitchen").data)
        self.assertNotIn(b"Trail Mug", self.client.get("/?q=%25").data)
        self.assertNotIn(b"Trail Mug", self.client.get("/?category=Outdoors").data)
        path = f"/manage/products/{product_id}/edit"
        csrf = self.token(self.client.get(path), "csrf")
        fields = {**self.fields, "csrf": csrf, "version": "1"}
        self.assertEqual(self.client.post("/manage/products/new", data=fields).status_code, 409)
        self.assertEqual(self.client.post(path, data={**fields, "price": "0.001"}).status_code, 422)
        self.assertEqual(self.client.post(path, data={**fields, "name": "Updated Mug"}).status_code, 303)
        self.assertEqual(self.client.post(path, data=fields).status_code, 409)
        self.assertIn(b"Updated Mug", self.client.get("/manage").data)
        delete = f"/manage/products/{product_id}/delete"
        self.assertEqual(self.client.post(delete, data={"csrf": csrf, "version": "2"}).status_code, 303)
        self.assertEqual(self.client.get(f"/products/{product_id}").status_code, 404)

    def test_supplied_csv_import_is_safe_and_repeatable(self):
        response = self.upload(FIXTURE.read_bytes())
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"88 added", response.data)
        self.assertIn(b"3 duplicates", response.data)
        self.assertIn(b"4 invalid", response.data)
        self.assertIn(b"2 blank", response.data)
        self.assertIn(b"Product names must be plain text without HTML tags.", response.data)
        self.assertIn(b"0 added", self.upload(FIXTURE.read_bytes()).data)
        self.assertIn(b"No matching products", self.client.get("/?q=XS-001").data)
        sql_probe = self.client.get("/", query_string={"q": "Robert'); DROP TABLE products;--"})
        self.assertIn(b"SQL-001", sql_probe.data)
        with self.app.app_context():
            db = get_db()
            self.assertEqual(db.execute("SELECT price_cents FROM products WHERE sku = 'WM-042'").fetchone()[0], 2999)
            self.assertEqual(db.execute("SELECT price_cents FROM products WHERE sku = 'YM-015'").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT stock FROM products WHERE sku = 'RS-001'").fetchone()[0], 150)
            self.assertIsNone(db.execute("SELECT weight_grams FROM products WHERE sku = 'GK-088'").fetchone()[0])
            self.assertEqual(db.execute("SELECT category FROM products WHERE sku = 'GC-025'").fetchone()[0], "Uncategorized")

    def test_product_forms_reject_markup_in_names_and_escape_descriptions(self):
        payload = "<script>alert('xss')</script>"
        csrf = self.token(self.client.get("/manage/products/new"), "csrf")
        invalid = {**self.fields, "name": payload, "csrf": csrf}
        create = self.client.post("/manage/products/new", data=invalid)
        self.assertEqual(create.status_code, 422)
        self.assertIn(b"Product names must be plain text without HTML tags.", create.data)
        self.assertNotIn(b"<script>", create.data)
        product_id = self.add_product(name="O'Brien's Trail Mug", description=payload)
        edit = self.client.post(f"/manage/products/{product_id}/edit", data={**invalid, "version": "1"})
        self.assertEqual(edit.status_code, 422)
        product = self.client.get(f"/products/{product_id}")
        self.assertIn(b"O&#39;Brien", product.data)
        self.assertIn(b"&lt;script&gt;", product.data)
        self.assertNotIn(b"<script>", product.data)

    def test_bad_csv_structure_leaves_database_unchanged(self):
        broken = HEADER + b'First,A-1,,Kitchen,1,1,0\n"unclosed'
        self.assertEqual(self.upload(broken).status_code, 422)
        self.assertIn(b"The catalog is empty", self.client.get("/").data)

    def test_csrf_and_host_validation(self):
        self.assertEqual(self.client.post("/manage/products/new", data=self.fields).status_code, 400)
        self.assertEqual(self.client.get("/", base_url="http://example.test").status_code, 400)

    def test_checkout_replay_decrements_stock_once_and_receipt_survives_deletion(self):
        product_id = self.add_product()
        path = f"/products/{product_id}"
        fields = self.checkout_form(self.client, product_id, "2")
        first = self.client.post(path, data=fields)
        replay = self.client.post(path, data=fields)
        self.assertEqual(first.status_code, 303)
        self.assertEqual(first.location, replay.location)
        self.assertEqual(self.client.post(path, data={**fields, "quantity": "1"}).status_code, 409)
        self.assertIn(b"out of stock", self.client.get(path).data)
        self.assertEqual(self.client.post(
            f"/manage/products/{product_id}/delete", data={"csrf": fields["csrf"], "version": "2"},
        ).status_code, 303)
        self.assertIn(b"$25.98", self.client.get(first.location).data)
        with self.app.app_context():
            self.assertEqual(get_db().execute("SELECT count(*) FROM orders").fetchone()[0], 1)

    def test_committed_checkout_survives_expiry_and_restart_but_unused_quote_expires(self):
        config = {**self.app.config, "SECRET_KEY": None}
        self.app = create_app(config)
        self.client = self.app.test_client()
        product_id = self.add_product()
        path = f"/products/{product_id}"
        with patch.object(TimestampSigner, "get_timestamp", return_value=10_000):
            ordered = self.checkout_form(self.client, product_id)
            unused = self.checkout_form(self.client, product_id)
            first = self.client.post(path, data=ordered)
        self.assertEqual(first.status_code, 303)
        cookie = self.client.get_cookie("session")

        restarted = create_app(config)
        client = restarted.test_client()
        client.set_cookie("session", cookie.value)
        with patch.object(TimestampSigner, "get_timestamp", return_value=11_000):
            replay = client.post(path, data=ordered)
            self.assertEqual(replay.status_code, 303)
            self.assertEqual(replay.location, first.location)
            expired = client.post(path, data=unused)
            self.assertEqual(expired.status_code, 409)
            self.assertIn(b"This checkout expired", expired.data)
        with restarted.app_context():
            db = get_db()
            self.assertEqual(db.execute("SELECT count(*) FROM orders").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT stock FROM products").fetchone()[0], 1)

    def test_dynamic_responses_are_uncached_and_static_css_revalidates(self):
        for path in ("/", "/manage", "/cart", "/healthz"):
            self.assertEqual(self.client.get(path).headers["Cache-Control"], "no-store")
        product_id = self.add_product()
        path = f"/products/{product_id}"
        checkout = self.client.get(path)
        self.assertEqual(checkout.headers["Cache-Control"], "no-store")
        fields = {
            "csrf": self.token(checkout, "csrf"),
            "quote": self.token(checkout, "quote"),
            "quantity": "1",
        }
        redirect = self.client.post(path, data=fields)
        self.assertEqual(redirect.status_code, 303)
        self.assertEqual(redirect.headers["Cache-Control"], "no-store")
        self.assertEqual(self.client.get(redirect.location).headers["Cache-Control"], "no-store")
        with self.client.get("/static/app.css") as asset:
            self.assertIn("no-cache", asset.headers["Cache-Control"])
            tag = asset.headers["ETag"]
        with self.client.get("/static/app.css", headers={"If-None-Match": tag}) as unchanged:
            self.assertEqual(unchanged.status_code, 304)

    def test_concurrent_duplicate_submissions_return_one_receipt(self):
        product_id = self.add_product(stock="2")
        fields = self.checkout_form(self.client, product_id)
        cookie = self.client.get_cookie("session")
        start = threading.Barrier(2, timeout=5)

        def submit():
            with self.app.test_client() as client:
                client.set_cookie("session", cookie.value)
                start.wait()
                response = client.post(f"/products/{product_id}", data=fields)
                return response.status_code, response.location

        with ThreadPoolExecutor(max_workers=2) as pool:
            requests = [pool.submit(submit) for _ in range(2)]
            first, replay = [request.result() for request in requests]
        self.assertEqual(first[0], 303)
        self.assertEqual(first, replay)
        with self.app.app_context():
            db = get_db()
            self.assertEqual(db.execute("SELECT count(*) FROM orders").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT stock FROM products").fetchone()[0], 1)

    def test_invalid_quantity_and_stale_price_create_no_order(self):
        product_id = self.add_product()
        path = f"/products/{product_id}"
        fields = self.checkout_form(self.client, product_id)
        self.assertEqual(self.client.post(path, data={**fields, "quantity": "0"}).status_code, 422)
        self.assertEqual(self.client.post(path, data={**fields, "quantity": "3"}).status_code, 409)
        response = self.client.post(
            f"/manage/products/{product_id}/edit",
            data={**self.fields, "price": "13.00", "csrf": fields["csrf"], "version": "1"},
        )
        self.assertEqual(response.status_code, 303)
        response = self.client.post(path, data=fields)
        self.assertEqual(response.status_code, 409)
        self.assertIn(b"The price changed", response.data)
        with self.app.app_context():
            db = get_db()
            self.assertEqual(db.execute("SELECT count(*) FROM orders").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT stock FROM products").fetchone()[0], 2)

    def test_concurrent_buyers_cannot_oversell(self):
        product_id = self.add_product(stock="1")
        start = threading.Barrier(2, timeout=5)

        def buy():
            with self.app.test_client() as client:
                fields = self.checkout_form(client, product_id)
                start.wait()
                return client.post(f"/products/{product_id}", data=fields).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = [pool.submit(buy) for _ in range(2)]
            self.assertEqual(sorted(response.result() for response in responses), [303, 409])
        with self.app.app_context():
            db = get_db()
            self.assertEqual(db.execute("SELECT count(*) FROM orders").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT stock FROM products").fetchone()[0], 0)

    def test_cart_update_remove_and_checkout_share_one_receipt_across_tabs(self):
        mug = self.add_product(stock="4")
        bottle = self.add_product(name="Trail Bottle", sku="B-1", stock="5", price="10.00")
        spare = self.add_product(name="Spare Cap", sku="C-1", price="2.00")
        self.change_cart(mug)
        self.change_cart(bottle)
        self.change_cart(spare)
        self.change_cart(mug, "update", "3")
        csrf = self.change_cart(spare, "remove")
        cart = self.client.get("/cart")
        self.assertIn(b"$48.97", cart.data)
        self.assertNotIn(b"Spare Cap", cart.data)
        first_form = {"csrf": csrf, "quote": self.token(cart, "quote")}
        next_timestamp = TimestampSigner("test").get_timestamp() + 1
        cookie = self.client.get_cookie("session")
        start = threading.Barrier(2, timeout=5)

        def submit(fields):
            with self.app.test_client() as client:
                client.set_cookie("session", cookie.value)
                start.wait()
                response = client.post("/cart/checkout", data=fields)
                return response.status_code, response.location

        with patch.object(TimestampSigner, "get_timestamp", return_value=next_timestamp):
            second_form = self.cart_form(csrf)
            self.assertNotEqual(first_form["quote"], second_form["quote"])
            with ThreadPoolExecutor(max_workers=2) as pool:
                first, replay = list(pool.map(submit, (first_form, second_form)))
        self.assertEqual(first[0], 303)
        self.assertEqual(first, replay)
        self.assertIn(b"Your cart is empty", self.client.get("/cart").data)
        receipt = self.client.get(first[1])
        for text in (b"Trail Mug", b"Trail Bottle", b"$48.97"):
            self.assertIn(text, receipt.data)
        with self.app.app_context():
            db = get_db()
            self.assertEqual(db.execute("SELECT count(*) FROM orders").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT count(*) FROM order_items").fetchone()[0], 2)
            self.assertEqual(
                [row[0] for row in db.execute("SELECT stock FROM products ORDER BY id")], [1, 4, 2],
            )

        self.change_cart(mug)
        retry = self.client.post("/cart/checkout", data=first_form)
        self.assertEqual(retry.location, first[1])
        self.assertIn(b"Trail Mug", self.client.get("/cart").data)

    def test_cart_checkout_rolls_back_every_item_when_one_sells_out(self):
        mug = self.add_product()
        bottle = self.add_product(name="Trail Bottle", sku="B-1")
        self.change_cart(mug)
        fields = self.cart_form(self.change_cart(bottle))
        with self.app.app_context():
            get_db().execute("UPDATE products SET stock = 0 WHERE id = ?", (bottle,))
        response = self.client.post("/cart/checkout", data=fields)
        self.assertEqual(response.status_code, 409)
        self.assertIn(b"Trail Bottle has only 0 units available", response.data)
        with self.app.app_context():
            db = get_db()
            self.assertEqual(db.execute("SELECT stock FROM products WHERE id = ?", (mug,)).fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT count(*) FROM orders").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM cart_items").fetchone()[0], 2)

    def test_cart_checkout_requires_review_after_price_or_quantity_changes(self):
        mug = self.add_product(stock="4")
        csrf = self.change_cart(mug)
        fields = self.cart_form(csrf)
        self.change_cart(mug, "update", "2")
        self.assertEqual(self.client.post("/cart/checkout", data=fields).status_code, 409)
        updated = self.cart_form(csrf)
        with self.app.app_context():
            get_db().execute("UPDATE products SET price_cents = 1500 WHERE id = ?", (mug,))
        stale = self.client.post("/cart/checkout", data=updated)
        self.assertEqual(stale.status_code, 409)
        self.assertIn(b"$30.00", stale.data)
        with self.app.app_context():
            db = get_db()
            self.assertEqual(db.execute("SELECT count(*) FROM orders").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT stock FROM products").fetchone()[0], 4)

    def test_existing_receipt_is_migrated_without_live_product(self):
        old_path = str(Path(self.folder.name) / "legacy.sqlite3")
        db = sqlite3.connect(old_path)
        try:
            db.executescript("""
                CREATE TABLE orders (
                    id TEXT PRIMARY KEY, product_id INTEGER NOT NULL, product_name TEXT NOT NULL,
                    sku TEXT NOT NULL, unit_price_cents INTEGER NOT NULL CHECK (unit_price_cents >= 0),
                    quantity INTEGER NOT NULL CHECK (quantity BETWEEN 1 AND 999999),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                ) STRICT;
                INSERT INTO orders VALUES ('legacy', 4, 'Old Mug', 'OLD-1', 895, 3, '2026-11-09 00:00:00');
            """)
        finally:
            db.close()
        config = {**self.app.config, "DATABASE": old_path}
        for _ in range(2):
            with create_app(config).test_client() as client:
                receipt = client.get("/orders/legacy")
                self.assertEqual(receipt.status_code, 200)
                self.assertIn(b"Old Mug", receipt.data)
                self.assertIn(b"$26.85", receipt.data)


if __name__ == "__main__":
    unittest.main()
