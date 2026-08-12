import logging

from app.logging_setup import setup_logging


class _App:
    def __init__(self, tmp_path, log_file="app.log"):
        self.config = {"TESTING": False, "LOG_LEVEL": "INFO", "LOG_FILE": log_file}
        self.instance_path = str(tmp_path)


def test_setup_logging_skips_testing(app):
    root = logging.getLogger()
    before = list(root.handlers)
    setup_logging(app)  # app do fixture tem TESTING=True
    assert list(root.handlers) == before


def test_setup_logging_creates_rotating_file(tmp_path):
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        # pytest pode adicionar handlers ao root logger — isola o teste
        root.handlers[:] = []
        setup_logging(_App(tmp_path))
        added = list(root.handlers)
        assert added

        logging.getLogger("test.logging").info("linha de teste")
        for h in added:
            h.flush()

        content = (tmp_path / "app.log").read_text(encoding="utf-8")
        assert "linha de teste" in content
        # RotatingFileHandler: 5MB, 3 backups
        from logging.handlers import RotatingFileHandler

        assert any(isinstance(h, RotatingFileHandler) and h.maxBytes == 5 * 1024 * 1024 for h in added)

        # segunda chamada não duplica handlers
        setup_logging(_App(tmp_path))
        assert len(root.handlers) == len(added)
    finally:
        root.handlers[:] = before
