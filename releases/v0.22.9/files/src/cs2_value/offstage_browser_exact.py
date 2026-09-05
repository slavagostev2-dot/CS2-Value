from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse
from typing import Callable

from .sources.offstage import OffstageAdapter


class OffstageBrowserError(RuntimeError):
    pass


_OFFSTAGE_BROWSER_TIMEZONE = "Europe/Moscow"
_OFFSTAGE_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/152.0.0.0 Safari/537.36"
)


def _new_offstage_page(browser):
    """Create an Offstage page in a fixed timezone.

    Offstage renders match clock times client-side. If Playwright inherits the
    host Windows timezone, the same match can display e.g. 21:00 on UTC+7 while
    the parser assumes Moscow time. Pinning the browser context to Europe/Moscow
    makes rendered timestamps deterministic and consistent with Offstage's
    schedule convention.
    """
    return browser.new_page(
        locale="ru-RU",
        timezone_id=_OFFSTAGE_BROWSER_TIMEZONE,
        user_agent=_OFFSTAGE_BROWSER_USER_AGENT,
    )


def _canonicalize_match_hrefs(hrefs: list[str]) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for href in hrefs:
        absolute = urljoin(OffstageAdapter.BASE_URL, href)
        parsed = urlparse(absolute)
        if parsed.netloc.lower() not in {"offstage.ru", "www.offstage.ru"}:
            continue
        path = parsed.path.rstrip("/")
        if not OffstageAdapter.MATCH_PATH_RE.match(path):
            continue
        canonical = f"https://www.offstage.ru{path}"
        if canonical not in seen:
            seen.add(canonical)
            found.append(canonical)
    return found


def _try_click_load_more(page) -> bool:
    """Click an obvious public 'load more' control if Offstage renders one.

    The exact UI copy may change, so several harmless Russian variants are tried.
    This helper never bypasses access controls; it only interacts with visible page controls.
    """
    labels = (
        "Показать ещё",
        "Показать еще",
        "Загрузить ещё",
        "Загрузить еще",
        "Ещё",
        "Еще",
    )
    for label in labels:
        for role in ("button", "link"):
            try:
                loc = page.get_by_role(role, name=label, exact=True)
                if loc.count() > 0 and loc.first.is_visible():
                    loc.first.click(timeout=2_000)
                    return True
            except Exception:
                continue
    return False


def discover_finished_urls_with_browser(
    listing_url: str,
    *,
    timeout_ms: int = 20_000,
    settle_ms: int = 1_500,
    target_count: int | None = None,
    max_load_rounds: int = 40,
    stable_rounds_before_stop: int = 5,
    progress: Callable[[str], None] | None = None,
) -> list[str]:
    """Discover finished Offstage match-card URLs from the JS-rendered listing.

    Offstage currently exposes the first batch client-side and may load older matches
    after scrolling or a visible "load more" action. We therefore keep loading until
    `target_count` is reached or several consecutive rounds produce no new match links.

    Playwright is imported lazily so the rest of the project does not require a browser.
    On Windows we intentionally prefer the user's installed Google Chrome (`channel="chrome"`)
    rather than downloading a separate bundled browser.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise OffstageBrowserError(
            "Browser discovery needs Playwright. Install the browser extra with: "
            'python -m pip install -e ".[dev,model,browser]"'
        ) from exc

    if progress:
        progress("Запускаю браузер для подгрузки истории Offstage...")
    with sync_playwright() as pw:
        browser = None
        launch_errors: list[str] = []
        for kwargs in (
            {"channel": "chrome", "headless": True},
            {"headless": True},
        ):
            try:
                browser = pw.chromium.launch(**kwargs)
                break
            except Exception as exc:  # pragma: no cover - depends on local browser installation
                launch_errors.append(f"{type(exc).__name__}: {exc}")
        if browser is None:
            raise OffstageBrowserError(
                "Could not start Chrome/Chromium for Offstage discovery. "
                "Chrome is preferred. Details: " + " | ".join(launch_errors)
            )

        try:
            page = _new_offstage_page(browser)
            page.goto(listing_url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(settle_ms)
            if progress:
                progress("Страница Offstage открыта. Ищу завершённые матчи...")

            try:
                finished = page.get_by_text("Завершены", exact=True)
                if finished.count() > 0:
                    finished.first.click(timeout=2_000)
                    page.wait_for_timeout(settle_ms)
            except Exception:
                pass

            stable_rounds = 0
            best: list[str] = []
            effective_rounds = max(1, max_load_rounds)
            if target_count is not None and target_count > 0:
                effective_rounds = max(
                    effective_rounds,
                    min(500, (target_count // 10) + 30),
                )
            for _ in range(effective_rounds):
                hrefs = page.locator("a[href]").evaluate_all(
                    "els => els.map(a => a.getAttribute('href')).filter(Boolean)"
                )
                current = _canonicalize_match_hrefs(hrefs)
                if len(current) > len(best):
                    best = current
                    stable_rounds = 0
                    if progress:
                        target_text = f"/{target_count}" if target_count is not None else ""
                        progress(f"Найдено ссылок: {len(best)}{target_text}")
                else:
                    stable_rounds += 1

                if target_count is not None and len(best) >= target_count:
                    return best[:target_count]

                clicked = _try_click_load_more(page)
                if clicked:
                    page.wait_for_timeout(1_200)
                else:
                    try:
                        page.mouse.wheel(0, 8_000)
                    except Exception:
                        pass
                    page.evaluate(
                        "window.scrollTo({top: document.body.scrollHeight, behavior: 'instant'})"
                    )
                    page.wait_for_timeout(1_200)

                if stable_rounds >= max(2, stable_rounds_before_stop):
                    if progress:
                        progress(
                            f"Новых ссылок больше не появляется после {stable_rounds} попыток. "
                            f"Останавливаю подгрузку на {len(best)} ссылках."
                        )
                    break

            if target_count is not None:
                return best[:target_count]
            return best
        finally:
            browser.close()


def find_match_urls_for_teams_with_browser(
    listing_url: str,
    team_a: str,
    team_b: str,
    *,
    timeout_ms: int = 20_000,
    settle_ms: int = 1_500,
    max_rounds: int = 6,
    progress: Callable[[str], None] | None = None,
) -> list[str]:
    """Find current Offstage match cards after client-side rendering.

    Unlike ``discover_finished_urls_with_browser`` this helper never clicks the
    Finished tab.  It is intended for current/scheduled/live Fonbet matching,
    where the static HTTP response may contain stale links while the useful
    match list is rendered by JavaScript.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise OffstageBrowserError(
            "Browser matching needs Playwright. Install the browser extra with: "
            'python -m pip install -e ".[dev,model,browser]"'
        ) from exc

    if progress:
        progress("Открываю текущий список Offstage в браузере для сопоставления матча...")

    with sync_playwright() as pw:
        browser = None
        launch_errors: list[str] = []
        for kwargs in (
            {"channel": "chrome", "headless": True},
            {"headless": True},
        ):
            try:
                browser = pw.chromium.launch(**kwargs)
                break
            except Exception as exc:
                launch_errors.append(f"{type(exc).__name__}: {exc}")
        if browser is None:
            raise OffstageBrowserError(
                "Could not start Chrome/Chromium for current Offstage matching. "
                "Details: " + " | ".join(launch_errors)
            )

        try:
            page = _new_offstage_page(browser)
            page.goto(listing_url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(settle_ms)

            for round_no in range(max(1, max_rounds)):
                html = page.content()
                found = OffstageAdapter.find_match_urls_for_teams(
                    html, team_a, team_b, listing_url
                )
                if found:
                    if progress:
                        progress(f"Браузер Offstage нашёл кандидатов: {len(found)}")
                    return found

                try:
                    page.mouse.wheel(0, 4_000)
                    page.evaluate(
                        "window.scrollTo({top: Math.min(document.body.scrollHeight, window.scrollY + 4000), behavior: 'instant'})"
                    )
                except Exception:
                    pass
                if round_no + 1 < max_rounds:
                    page.wait_for_timeout(1_000)
            return []
        finally:
            browser.close()


def fetch_match_with_browser(
    url: str,
    *,
    timeout_ms: int = 20_000,
    settle_ms: int = 1_500,
    max_wait_rounds: int = 6,
    progress: Callable[[str], None] | None = None,
):
    """Fetch and parse one JS-rendered Offstage match detail card.

    Current/scheduled Offstage detail pages can expose stale or incomplete text to
    plain HTTP while the authoritative start block and veto are rendered in the
    browser. Matching discovered through the browser must therefore validate the
    same rendered detail card instead of falling back to ``httpx`` for the final
    identity/time check.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise OffstageBrowserError(
            "Browser detail fetch needs Playwright. Install the browser extra with: "
            'python -m pip install -e ".[dev,model,browser]"'
        ) from exc

    if progress:
        progress("Открываю найденную карточку Offstage в браузере...")

    with sync_playwright() as pw:
        browser = None
        launch_errors: list[str] = []
        for kwargs in (
            {"channel": "chrome", "headless": True},
            {"headless": True},
        ):
            try:
                browser = pw.chromium.launch(**kwargs)
                break
            except Exception as exc:
                launch_errors.append(f"{type(exc).__name__}: {exc}")
        if browser is None:
            raise OffstageBrowserError(
                "Could not start Chrome/Chromium for Offstage detail validation. "
                "Details: " + " | ".join(launch_errors)
            )

        try:
            page = _new_offstage_page(browser)
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(settle_ms)

            visible_text = ""
            for round_no in range(max(1, max_wait_rounds)):
                try:
                    visible_text = page.locator("body").inner_text(timeout=5_000)
                except Exception:
                    visible_text = ""
                if re.search(r"Матч\s+.+?\s+vs\s+.+", visible_text, re.I) and re.search(
                    r"Начало\s+матча", visible_text, re.I
                ):
                    break
                if round_no + 1 < max_wait_rounds:
                    page.wait_for_timeout(750)

            if not visible_text.strip():
                raise OffstageBrowserError("Rendered Offstage detail page returned no visible text.")
            record = OffstageAdapter.parse_visible_text(visible_text, url)
            if progress:
                start = record.played_at.isoformat() if record.played_at is not None else "не найдено"
                progress(f"Отрисованная карточка Offstage: начало {start}")
            return record
        finally:
            browser.close()
