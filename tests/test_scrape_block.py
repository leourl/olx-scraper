from app.services import scrape_block


def test_get_blocked_until_missing_file(tmp_path):
    assert scrape_block.get_blocked_until(str(tmp_path)) == 0.0


def test_set_get_roundtrip(tmp_path):
    scrape_block.set_blocked(str(tmp_path), 12345.0)
    assert scrape_block.get_blocked_until(str(tmp_path)) == 12345.0
    assert (tmp_path / "scrape_block.json").exists()


def test_clear_removes(tmp_path):
    scrape_block.set_blocked(str(tmp_path), 12345.0)
    scrape_block.clear_blocked(str(tmp_path))
    assert scrape_block.get_blocked_until(str(tmp_path)) == 0.0
    assert not (tmp_path / "scrape_block.json").exists()


def test_corrupt_file_returns_zero(tmp_path):
    (tmp_path / "scrape_block.json").write_text("{nope", encoding="utf-8")
    assert scrape_block.get_blocked_until(str(tmp_path)) == 0.0
