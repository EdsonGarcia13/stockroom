# Stockroom

Stockroom is a local e-commerce demo. Browse and search the catalog, manage products, import CSV inventory, and place a simulated order for one or more products. SQLite stores inventory, carts, and receipts across restarts.

## Run with Docker

From this directory, with Docker Desktop or Docker Engine and Compose running:

```sh
docker compose up --build -d --wait
docker compose exec app flask --app stockroom import-csv data/challenge-products.csv
```

Open [Stockroom](http://127.0.0.1:8000). The second command is optional. To exercise the upload interface from an empty catalog, skip that command and choose **Manage products → Import CSV**, then upload `data/challenge-products.csv`.

The service listens on loopback. Set `STOCKROOM_PORT` before startup to use a different host port. Run `docker compose down` to stop the service. The named volume retains the database. Running `docker compose down --volumes` also deletes the database.

## Run with Python

Use Python 3.12 or newer. From the project root on Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m flask --app stockroom import-csv data/challenge-products.csv
.\.venv\Scripts\waitress-serve.exe --listen=127.0.0.1:8000 --call stockroom:create_app
```

On macOS or Linux, use `python3.12 -m venv .venv` and use `.venv/bin/python` and `.venv/bin/waitress-serve` in the remaining commands. The app creates its schema on startup. Native runs store data in `instance/stockroom.sqlite3` by default. Set `STOCKROOM_DATABASE` to use a different file. The default signing key persists with the database. Set `STOCKROOM_SECRET` to supply your own key for a native run.

## Try the main flows

1. In **Shop**, search for `RS-001` or select a category. Use **Add** on two products, then open **Cart**. Change a quantity with **Update**, or use **Remove**. Select **Place demo order** to get one receipt with all items. The catalog shows the new stock counts.
2. In **Manage products**, create a product, search for it by SKU, edit it, and use **Delete product** on the edit screen to remove it.
3. In **Import CSV**, upload the challenge file. Review the row numbers and reasons for duplicate and invalid records. Uploading it again preserves existing products and stock counts.

## Supplied CSV

Downloaded on **November 9, 2026, at 3:31 p.m. Mazatlán time (UTC−07:00)**. The original export is included at [data/challenge-products.csv](data/challenge-products.csv). [Assessment sample spreadsheet](https://docs.google.com/spreadsheets/d/13kKAvnOmMk6SovPrvOTlvG0oTz6-gUn8BUsxSEoV-UA/edit?gid=262019509).

On an empty database, the file adds **88 products**, skips **3 duplicate SKU rows** and **2 blank rows**, and rejects **4 invalid rows**. The importer accepts UTF-8 (with or without a BOM) and quoted fields. It caps input at 1 MB and 5,000 data rows. The seven named headers may appear in any order.

- The first valid occurrence of a new SKU wins. Existing SKUs are skipped so repeated imports cannot overwrite stock or other edits. SKUs are normalized to uppercase. Different SKUs may share a name.
- Prices accept up to two decimal places. CSV prices may include a leading dollar sign or use `free` for zero. Stock must be a nonnegative integer. Missing weight stays unknown, and a blank category becomes `Uncategorized`.
- Invalid rows are reported by their starting line number. The app validates the entire file structure before writing, so an invalid header, undecodable file, or malformed CSV leaves the database untouched. Valid rows from a readable file are committed together.
- Product names must be plain text. Import and product forms reject names with HTML tags, including the supplied XSS test row. Jinja still escapes all product text when rendering pages. The supplied SQL test string is kept as literal data to exercise parameterized database queries.

## Decisions and alternatives

| Question | Decision and tradeoff |
| --- | --- |
| What does the local deployment need? | Flask, server-rendered forms, Waitress, and one SQLite file keep startup and review simple. A React client and JSON API would be useful for a richer client, with a separate build and API surface to maintain. |
| What happens when two buyers request the same stock? | Checkout uses one immediate SQLite write transaction to verify the prices, deduct stock, save every order item, and clear the cart. If any item is unavailable, the entire purchase rolls back. SQLite serializes writes. PostgreSQL would be a better fit for sustained concurrent writes or multiple application hosts. [SQLite transaction behavior](https://www.sqlite.org/lang_transaction.html). |
| What if a form is stale or a request is replayed? | A signed quote can create an order for 15 minutes. Tabs showing the same cart share a checkout ID. Replaying that quote for the same items and prices returns the original receipt, including after expiration or restart with the same session and signing key. Changing the cart or its prices requires another review. Product edits and deletes compare a version to protect inventory from stale forms. |
| What should be cached? | Catalog pages, checkout, receipts, and redirects use `Cache-Control: no-store` so sessions and current stock stay fresh. Browsers can keep static files and revalidate them using ETags. SQLite serves current inventory directly, which keeps cache invalidation and an extra service out of the local deployment. |
| How is the interface styled? | Stockroom uses Jinja and native HTML controls with the precompiled [Astryx Neutral theme](https://astryx.atmeta.com/docs/styling-overview) (version 0.7.12, MIT). The CSS ships locally with the app. A small checkout script previews the total and gives feedback while submitting. Checkout and validation work through the server. |
| How should money and order history be stored? | Prices use integer cents and weights use integer grams. Each order has item snapshots of product name, SKU, quantity, and price. Startup migrates older single-product orders into the same format and keeps their receipt URLs. |
| Should imports update matching products or reject the whole batch? | The importer skips duplicate SKUs and reports bad rows so reviewers can use the clean records in the supplied file. An inventory-sync mode would need explicit update rules, especially for stock. |
| How far should search go? | Paginated substring search covers name, SKU, description, and category, with an exact category filter. This is enough for the sample catalog. Full-text indexing would make sense for relevance ranking or larger catalogs. |

The demo assumes a trusted local operator and USD pricing. The cart belongs to the current browser session and can hold up to 50 distinct products. Stock is deducted when checkout commits. The app simulates an approved payment and collects no card details. CSRF protection and host checks cover the local browser workflows. Public deployment would require authenticated administration, customer identity and order authorization, HTTPS, operational backups, and a payment integration.

## Verify

With the dependencies installed, run:

```sh
python -m unittest discover -s tests -v
```

Use `.\.venv\Scripts\python.exe` on Windows or `.venv/bin/python` on macOS and Linux if the environment is inactive. The suite uses temporary databases and covers CRUD, search, CSV validation, cache headers, cart edits, stale prices, concurrent checkout, rollback when an item sells out, checkout replay, and migration of existing receipts.

AI assistance was used during implementation. Design rationale is recorded here.
