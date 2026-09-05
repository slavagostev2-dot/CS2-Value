from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .db import connect, insert_veto_snapshot, upsert_match
from .fonbet_analysis import FonbetOffstageLink, FonbetOffstageResolutionError, resolve_fonbet_to_offstage
from .offstage_browser import fetch_match_with_browser


@dataclass(frozen=True)
class VetoCapture:
    link: FonbetOffstageLink
    record: object
    captured_at: str
    snapshot_id: int | None


def capture_visible_veto(
    db_path: str | Path,
    link: FonbetOffstageLink,
    *,
    detail_fetcher=fetch_match_with_browser,
    progress: Callable[[str], None] | None = None,
) -> VetoCapture:
    """Capture one authoritative JS-rendered Offstage detail state.

    Current/scheduled Offstage cards can be incomplete over plain HTTP. Veto capture
    therefore uses the same browser-rendered detail path that validates match time.
    """
    if detail_fetcher is fetch_match_with_browser:
        fresh = detail_fetcher(link.offstage_url, progress=progress)
    else:
        fresh = detail_fetcher(link.offstage_url)
    captured_at = datetime.now(timezone.utc).isoformat()
    with connect(db_path) as conn:
        match_id = upsert_match(conn, fresh)
        snapshot_id = insert_veto_snapshot(
            conn,
            match_id=match_id,
            captured_at=captured_at,
            source="offstage-browser",
            status_at_capture=fresh.status,
            actions=fresh.veto,
        )
        conn.commit()
    return VetoCapture(link=link, record=fresh, captured_at=captured_at, snapshot_id=snapshot_id)


def _seconds_until_start(record, now: datetime) -> float | None:
    played_at = getattr(record, "played_at", None)
    if played_at is None:
        return None
    return (played_at.astimezone(timezone.utc) - now).total_seconds()


def _adaptive_sleep_seconds(seconds_until_start: float | None, base_interval: int) -> int:
    if seconds_until_start is None:
        return max(60, base_interval)
    if seconds_until_start > 3 * 3600:
        return max(600, base_interval)
    if seconds_until_start > 90 * 60:
        return max(300, base_interval)
    if seconds_until_start > 20 * 60:
        return max(120, base_interval)
    return max(60, min(base_interval, 120))


def watch_veto_fonbet(
    db_path: str | Path,
    fonbet_url: str,
    *,
    interval_seconds: int = 120,
    stop_after_start_minutes: int = 15,
    progress: Callable[[str], None] | None = None,
    resolver=resolve_fonbet_to_offstage,
    detail_fetcher=fetch_match_with_browser,
    sleep_fn=time.sleep,
    now_fn=lambda: datetime.now(timezone.utc),
) -> None:
    """Keep timestamping rendered veto changes until shortly after match start.

    Identical states are deduplicated by the database layer, so frequent polling does
    not create duplicate snapshots. Polling slows down automatically when the match is
    still hours away and becomes more frequent near start.
    """
    emit = progress or (lambda _message: None)
    link: FonbetOffstageLink | None = None
    last_snapshot_id: int | None = None

    while True:
        now = now_fn()
        if link is None:
            try:
                link, _ = resolver(db_path, fonbet_url, progress=emit)
                emit(f"Матч сопоставлен: {link.offstage_url}")
            except FonbetOffstageResolutionError as exc:
                emit(f"Сопоставление пока не найдено: {exc}")
                sleep_fn(max(300, interval_seconds))
                continue

        try:
            capture = capture_visible_veto(
                db_path,
                link,
                detail_fetcher=detail_fetcher,
                progress=emit,
            )
        except Exception as exc:
            emit(f"Не удалось обновить карточку Offstage: {type(exc).__name__}: {exc}")
            sleep_fn(max(60, interval_seconds))
            continue

        record = capture.record
        seconds_to_start = _seconds_until_start(record, now)
        veto = getattr(record, "veto", ())
        if veto:
            if capture.snapshot_id is not None and capture.snapshot_id != last_snapshot_id:
                last_snapshot_id = capture.snapshot_id
                emit(
                    f"VETO обновилось: {len(veto)} действий; "
                    f"snapshot_id={capture.snapshot_id}; captured_at={capture.captured_at}"
                )
                for action in veto:
                    who = f" — {action.team_name}" if action.team_name else ""
                    emit(f"  {action.action_order}. {action.action.upper()}: {action.map_name}{who}")
            else:
                emit(f"Veto без изменений: {len(veto)} действий.")
        else:
            emit("Veto/picks пока не видны.")

        if seconds_to_start is not None and seconds_to_start < -(stop_after_start_minutes * 60):
            emit(
                f"Останавливаю наблюдение: прошло больше {stop_after_start_minutes} мин после официального старта."
            )
            return

        sleep_seconds = _adaptive_sleep_seconds(seconds_to_start, interval_seconds)
        if seconds_to_start is None:
            emit(f"Следующая проверка через {sleep_seconds // 60} мин.")
        elif seconds_to_start > 0:
            emit(
                f"До старта примерно {seconds_to_start / 60:.0f} мин; "
                f"следующая проверка через {sleep_seconds // 60} мин."
            )
        else:
            emit(f"Матч уже должен был начаться; следующая проверка через {sleep_seconds // 60} мин.")
        sleep_fn(sleep_seconds)
