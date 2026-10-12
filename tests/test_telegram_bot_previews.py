import hashlib

from telegram_bot_previews import TelegramPreviews


PNG = b"\x89PNG\r\n\x1a\n" + b"fixture"


def test_preview_cache_isolated_by_project_and_session():
    previews = TelegramPreviews()
    assert previews.record(project="P1", session="S1", approval="a1", image=PNG)
    assert previews.message("P1", "S1")["sha256"] == hashlib.sha256(PNG).hexdigest()
    assert previews.message("P2", "S1") is None
    assert previews.message("P1", "S2") is None


def test_preview_cache_ttl_and_capacity_are_bounded():
    now = [10.0]
    previews = TelegramPreviews(max_entries=2, ttl=5, clock=lambda: now[0])
    previews.record(project="P", session="S1", approval="a1", image=PNG)
    previews.record(project="P", session="S2", approval="a2", image=PNG)
    previews.record(project="P", session="S3", approval="a3", image=PNG)
    assert previews.message("P", "S1") is None
    assert previews.message("P", "S3") is not None
    now[0] = 15.0
    assert previews.message("P", "S3") is None


def test_preview_rejects_non_png_and_out_of_bound_bytes():
    previews = TelegramPreviews()
    assert not previews.record(project="P", session="S", approval="a", image=b"not-png")
    assert not previews.record(project="P", session="S", approval="a", image=b"\x89PNG\r\n\x1a\n" + b"x" * (8 * 1024 * 1024))
    assert previews.message("P", "S") is None
