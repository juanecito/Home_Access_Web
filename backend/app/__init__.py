import getpass

import click
from flask import Flask, jsonify
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from . import admin, auth, db, item_types, items, terminal, users
from .activity import record
from .config import Config


def create_app(overrides=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    if overrides:
        app.config.update(overrides)
    if not app.config.get("SECRET_KEY") or len(app.config["SECRET_KEY"]) < 32:
        raise RuntimeError("HOMEACCES_SECRET_KEY must be set to a random string of at least 32 characters")

    # nginx runs in front of the app; trust exactly one proxy hop for client IP / scheme.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

    app.teardown_appcontext(db.close_db)
    # Create missing tables and migrate older databases (e.g. add users.avatar) before serving.
    with app.app_context():
        db.init_db()
    app.register_blueprint(auth.bp)
    app.register_blueprint(items.bp)
    app.register_blueprint(item_types.bp)
    app.register_blueprint(users.bp)
    app.register_blueprint(terminal.bp)
    app.register_blueprint(admin.bp)

    @app.get("/api/health")
    def health():
        db.get_db().execute("SELECT 1")
        return jsonify(status="ok")

    @app.errorhandler(HTTPException)
    def handle_http_error(err):
        return jsonify(error=err.description, status=err.code), err.code

    @app.after_request
    def no_store(resp):
        resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    _register_cli(app)
    return app


def _register_cli(app):
    @app.cli.command("init-db")
    def init_db_command():
        """Create the database tables (safe to run repeatedly)."""
        db.init_db()
        click.echo(f"Database initialised at {app.config['DATABASE']}")

    @app.cli.command("create-admin")
    @click.argument("username")
    def create_admin_command(username):
        """Create an admin user, or promote and reset the password of an existing one."""
        auth.validate_username(username)
        db.init_db()
        conn = db.get_db()
        existing = conn.execute("SELECT auth_source FROM users WHERE username = ?", (username,)).fetchone()
        if existing and existing["auth_source"] != "local":
            raise click.ClickException(f"'{username}' is a system (PAM) account; use a different name")
        password = getpass.getpass("Password: ")
        if password != getpass.getpass("Repeat password: "):
            raise click.ClickException("passwords do not match")
        auth.validate_password(password)

        conn.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, 'admin') "
            "ON CONFLICT(username) DO UPDATE SET password_hash = excluded.password_hash, role = 'admin'",
            (username, auth.hash_password(password)),
        )
        conn.commit()
        record("user.create", username="(command line)", target=username, role="admin",
               reason="create-admin: created or password reset")
        click.echo(f"Admin user '{username}' is ready")
