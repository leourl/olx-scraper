import pytest

from app import create_app


class TestConfig:
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"timeout": 30}}
    USER_AGENT = "test-agent"
    SCRAPER_DELAY = 0.0
    SCRAPER_MAX_PAGES = 3
    SCRAPER_TIMEOUT = 10
    SCRAPER_IMPERSONATE = "chrome"
    SCRAPER_BLOCK_COOLDOWN_MINUTES = 0
    DEEPSEEK_KEY = ""
    DEEPSEEK_BASE_URL = "https://api.deepseek.com"
    DEEPSEEK_MODEL = "deepseek-v4-flash"
    LLM_TIMEOUT = 60
    LLM_REASONING_EFFORT = "none"
    LLM_MAX_CHARS = 1500
    LLM_MAX_RETRIES = 2
    AUTORUN_ENABLED = False
    AUTORUN_INTERVAL_MINUTES = 120
    LOG_LEVEL = "WARNING"
    LOG_FILE = ""


@pytest.fixture()
def app():
    app = create_app(TestConfig)
    with app.app_context():
        from app.extensions import db

        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()
