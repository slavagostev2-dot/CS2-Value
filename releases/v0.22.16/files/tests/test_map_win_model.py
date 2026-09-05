from datetime import datetime, timedelta, timezone

from cs2_value.db import connect, init_db
from cs2_value.map_win_model import (
    backtest_map_model,
    build_map_feature_rows,
    compare_map_win_upgrade,
    series_win_probability,
)


def _insert_match(conn, idx, when, maps, *, team_a="Alpha", team_b="Beta"):
    score_a = sum(1 for _, a, b in maps if a > b)
    score_b = sum(1 for _, a, b in maps if b > a)
    conn.execute(
        """
        INSERT INTO matches(
          source, source_url, source_match_key, played_at, team_a, team_b,
          best_of, tournament, status, team_a_score, team_b_score
        ) VALUES ('test', ?, ?, ?, ?, ?, 3, 'T', 'finished', ?, ?)
        """,
        (f"https://x/{idx}", str(idx), when.isoformat(), team_a, team_b, score_a, score_b),
    )
    match_id = conn.execute(
        "SELECT id FROM matches WHERE source='test' AND source_match_key=?", (str(idx),)
    ).fetchone()["id"]
    for order, (name, a, b) in enumerate(maps, start=1):
        conn.execute(
            """INSERT INTO maps(match_id, map_order, map_name, team_a_score, team_b_score)
               VALUES (?, ?, ?, ?, ?)""",
            (match_id, order, name, a, b),
        )
    return int(match_id)


def test_series_win_probability_matches_bo3_formula():
    p1, p2, p3 = 0.60, 0.40, 0.55
    expected = p1 * p2 + p1 * (1 - p2) * p3 + (1 - p1) * p2 * p3
    assert abs(series_win_probability([p1, p2, p3]) - expected) < 1e-12


def test_same_series_maps_do_not_update_each_other(tmp_path):
    db = tmp_path / "maps.db"
    init_db(db)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with connect(db) as conn:
        _insert_match(conn, 1, start, [("Anubis", 13, 8), ("Mirage", 7, 13), ("Cache", 13, 9)])
        target_id = _insert_match(
            conn,
            2,
            start + timedelta(days=1),
            [("Anubis", 13, 10), ("Anubis", 9, 13), ("Cache", 13, 11)],
        )
        conn.commit()

    rows = [row for row in build_map_feature_rows(db) if row.match_id == target_id]
    anubis = [row for row in rows if row.map_name == "anubis"]
    assert len(anubis) == 2
    assert anubis[0].same_map_history_a == anubis[1].same_map_history_a == 1
    assert anubis[0].same_map_history_b == anubis[1].same_map_history_b == 1
    f0 = {k: v for k, v in anubis[0].features.items() if not k.startswith("map_order_")}
    f1 = {k: v for k, v in anubis[1].features.items() if not k.startswith("map_order_")}
    assert f0 == f1


def test_future_match_does_not_change_past_map_features(tmp_path):
    db = tmp_path / "future.db"
    init_db(db)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with connect(db) as conn:
        _insert_match(conn, 1, start, [("Anubis", 13, 8), ("Mirage", 7, 13), ("Cache", 13, 9)])
        target_id = _insert_match(
            conn, 2, start + timedelta(days=1), [("Anubis", 9, 13), ("Mirage", 13, 7), ("Cache", 11, 13)]
        )
        conn.commit()

    before = [row for row in build_map_feature_rows(db) if row.match_id == target_id]
    with connect(db) as conn:
        _insert_match(
            conn, 3, start + timedelta(days=2), [("Anubis", 13, 0), ("Mirage", 13, 0), ("Cache", 13, 0)]
        )
        conn.commit()
    after = [row for row in build_map_feature_rows(db) if row.match_id == target_id]
    assert [(r.map_name, r.features, r.same_map_history_a, r.same_map_history_b) for r in before] == [
        (r.map_name, r.features, r.same_map_history_a, r.same_map_history_b) for r in after
    ]


def _populate_many(db, n=50):
    init_db(db)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with connect(db) as conn:
        for i in range(n):
            # Same-map tendencies are strong but overall series strength is deliberately mixed.
            anubis_a = i % 5 != 0
            mirage_a = i % 5 == 0
            cache_a = i % 2 == 0
            maps = [
                ("Anubis", 13 if anubis_a else 8, 8 if anubis_a else 13),
                ("Mirage", 13 if mirage_a else 8, 8 if mirage_a else 13),
                ("Cache", 13 if cache_a else 10, 10 if cache_a else 13),
            ]
            _insert_match(conn, i + 1, start + timedelta(days=i), maps)
        conn.commit()


def test_walk_forward_prediction_group_uses_one_train_size_per_match(tmp_path):
    db = tmp_path / "walk.db"
    _populate_many(db, 30)
    result = backtest_map_model(db, mode="map", min_train_maps=20, retrain_every_matches=3)
    assert result.evaluated_maps > 0
    by_match = {}
    for prediction in result.predictions:
        by_match.setdefault(prediction.match_id, set()).add(prediction.train_maps)
    assert all(len(sizes) == 1 for sizes in by_match.values())


def test_map_win_upgrade_report_runs_on_small_history(tmp_path):
    db = tmp_path / "report.db"
    _populate_many(db, 55)
    report = compare_map_win_upgrade(
        db,
        model_min_train_maps=20,
        model_retrain_every_matches=3,
        calibration_min_train_predictions=30,
        calibration_retrain_every_matches=3,
    )
    assert report.coverage.valid_maps == 55 * 3
    assert report.general.evaluated_maps > 0
    assert report.map_specific.evaluated_maps == report.general.evaluated_maps
    assert report.map_specific.platt_metrics.n == report.map_specific.evaluated_maps
