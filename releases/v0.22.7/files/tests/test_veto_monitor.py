from datetime import datetime, timezone

from cs2_value.db import connect, init_db
from cs2_value.fonbet_analysis import FonbetOffstageLink
from cs2_value.models import MatchRecord, VetoAction
from cs2_value.veto_monitor import _adaptive_sleep_seconds, capture_visible_veto


def test_capture_visible_veto_uses_rendered_detail_fetcher(tmp_path):
    db = tmp_path / 'rendered.db'
    init_db(db)
    link = FonbetOffstageLink(
        match_id=1,
        fonbet_event_id='777',
        fonbet_team_a='Alpha', fonbet_team_b='Beta',
        offstage_url='https://www.offstage.ru/cs2/matches/alpha-vs-beta',
        offstage_team_a='Alpha', offstage_team_b='Beta',
        reversed_orientation=False, time_difference_seconds=0.0,
        match_status='scheduled',
    )
    calls = []
    rendered = MatchRecord(
        source='offstage', source_url=link.offstage_url, source_match_key='alpha-vs-beta',
        played_at=datetime(2026, 9, 5, 14, 0, tzinfo=timezone.utc),
        team_a='Alpha', team_b='Beta', best_of=3, tournament='T', status='scheduled',
        veto=(VetoAction('Nuke', 'pick', 'Alpha', 1, 1),),
    )

    def detail_fetcher(url):
        calls.append(url)
        return rendered

    capture = capture_visible_veto(db, link, detail_fetcher=detail_fetcher)
    assert calls == [link.offstage_url]
    assert capture.snapshot_id is not None
    with connect(db) as conn:
        row = conn.execute('SELECT source FROM veto_snapshots').fetchone()
        action = conn.execute('SELECT action,map_name,team_name FROM veto_actions').fetchone()
    assert row['source'] == 'offstage-browser'
    assert (action['action'], action['map_name'], action['team_name']) == ('pick', 'Nuke', 'Alpha')


def test_veto_watch_adapts_polling_frequency():
    assert _adaptive_sleep_seconds(4 * 3600, 120) == 600
    assert _adaptive_sleep_seconds(2 * 3600, 120) == 300
    assert _adaptive_sleep_seconds(45 * 60, 120) == 120
    assert _adaptive_sleep_seconds(10 * 60, 120) == 120
