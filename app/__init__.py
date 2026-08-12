from flask import Flask

from app.config import Config
from app.extensions import db, migrate


def create_app(config_object: object = Config) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_object)

    db.init_app(app)
    migrate.init_app(app, db)

    from app import cli
    from app.models import Ad, AdImage, AdSpec, RunHistory  # noqa: F401
    from app.services.runner import RunManager

    app.extensions["run_manager"] = RunManager()

    cli.register(app)

    from app.blueprints.api import bp as api_bp
    from app.blueprints.main import bp as main_bp

    app.register_blueprint(api_bp)
    app.register_blueprint(main_bp)

    if not app.config.get("TESTING"):
        from app.logging_setup import setup_logging

        setup_logging(app)

    if not app.config.get("TESTING") and _in_main_process(app):
        from sqlalchemy.exc import OperationalError

        from app.services.run_history_service import mark_running_as_interrupted

        with app.app_context():
            try:
                mark_running_as_interrupted()
            except OperationalError:
                app.logger.warning("tabela run_history ausente — deploy inicial (migração pendente)")

    if app.config["AUTORUN_ENABLED"] and _in_main_process(app):
        from app.services.autoscheduler import start_scheduler

        start_scheduler(app)

    return app


def _in_main_process(app) -> bool:
    """Evita scheduler duplicado com o reloader do dev server (debug=True)."""
    import os

    if not app.debug:
        return True
    return os.environ.get("WERKZEUG_RUN_MAIN") == "true"
