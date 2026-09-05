from cs2_value.offstage_browser import (
    _OFFSTAGE_BROWSER_TIMEZONE,
    _new_offstage_page,
)


def test_offstage_browser_page_is_pinned_to_moscow_timezone():
    class FakeBrowser:
        def __init__(self):
            self.kwargs = None

        def new_page(self, **kwargs):
            self.kwargs = kwargs
            return object()

    browser = FakeBrowser()
    page = _new_offstage_page(browser)

    assert page is not None
    assert _OFFSTAGE_BROWSER_TIMEZONE == "Europe/Moscow"
    assert browser.kwargs["timezone_id"] == "Europe/Moscow"
    assert browser.kwargs["locale"] == "ru-RU"
