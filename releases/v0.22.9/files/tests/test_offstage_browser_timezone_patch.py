from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_offstage_playwright_contexts_are_pinned_to_moscow():
    source = (ROOT / "src" / "cs2_value" / "offstage_browser.py").read_text(encoding="utf-8")
    assert source.count('timezone_id="Europe/Moscow"') == 3
