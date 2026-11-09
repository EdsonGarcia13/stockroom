import os
import secrets
import sqlite3
from pathlib import Path

import click
from flask import Flask, render_template
from werkzeug.exceptions import SecurityError

from .catalog import ValidationError, format_weight, import_products
from .db import close_db, get_db, init_db


def create_app(config=None):
    from .views import web

    app = Flask(__name__)
    app.config.from_mapping(
        DATABASE=os.environ.get("STOCKROOM_DATABASE", str(Path(app.instance_path) / "stockroom.sqlite3")),
        SECRET_KEY=os.environ.get("STOCKROOM_SECRET") or secrets.token_hex(32),
        MAX_CONTENT_LENGTH=1_100_000,
        MAX_FORM_MEMORY_SIZE=128_000,
        MAX_FORM_PARTS=16,
        SESSION_COOKIE_SAMESITE="Lax",
        TRUSTED_HOSTS=["localhost", "127.0.0.1", "[::1]"],
    )
    if config:
        app.config.update(config)
    Path(app.config["DATABASE"]).parent.mkdir(parents=True, exist_ok=True)
    app.teardown_appcontext(close_db)
    app.register_blueprint(web)
    app.add_template_filter(format_weight, "weight_kg")
    with app.app_context():
        init_db()

    @app.template_filter()
    def money(cents):
        dollars, remainder = divmod(cents, 100)
        return f"${dollars:,}.{remainder:02d}"

    @app.after_request
    def protect_response(response):
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if response.mimetype == "text/html":
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(400)
    @app.errorhandler(404)
    @app.errorhandler(413)
    def request_error(error):
        message = {
            400: "This request could not be processed. Reload the page and try again.",
            404: "This page or product could not be found.",
            413: "The upload is too large. Choose a CSV of 1 MB or smaller.",
        }[error.code]
        return render_template("error.html", title=error.name, message=message), error.code

    @app.errorhandler(SecurityError)
    def invalid_host(error):
        return "Bad request.", 400

    @app.errorhandler(sqlite3.OperationalError)
    def database_error(error):
        code = getattr(error, "sqlite_errorcode", 0) & 0xFF
        if code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            return (
                render_template("error.html", title="Catalog busy", message="Wait a moment and try again."),
                503,
                {"Retry-After": "1"},
            )
        app.logger.error("Database operation failed", exc_info=error)
        return render_template("error.html", title="Service error", message="The database request failed."), 500

    @app.cli.command("import-csv")
    @click.argument("path", type=click.File("rb"))
    def import_csv_command(path):
        try:
            report = import_products(get_db(), path.read(1_000_001))
        except ValidationError as error:
            raise click.ClickException(str(error)) from error
        click.echo(
            f"Imported {report.inserted}, skipped {report.duplicates} duplicates and {report.blank} blank rows, "
            f"rejected {report.invalid} invalid rows."
        )
        for line, message in report.issues:
            click.echo(f"Line {line}: {message}")

    return app
