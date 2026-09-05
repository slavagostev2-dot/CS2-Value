from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from .db import connect
from .elo import expected_score
from .identity import canonical_key
from .metrics import probability_metrics
from .map_win_model import (
    GENERAL_FEATURES, MAP_SPECIFIC_FEATURES, MapBacktestResult, MapFeatureRow,
    MapPrediction, _MapTeamState, _features_for_map, _map_deque,
)

def build_map_feature_rows_impl(
    db_path: str | Path,
    *,
    initial_rating: float = 1500.0,
    match_k_factor: float = 24.0,
    map_k_factor: float = 18.0,
    elo_scale: float = 400.0,
) -> list[MapFeatureRow]:
    """Build conditional map-outcome rows using only history before each series.

    The historical map name/order is used as the *conditional target context*.
    All maps from one match are featurized before any result from that same match
    updates state. This prevents map 1 from leaking into a prematch prediction for
    map 2/3. Historical veto availability is not claimed by this backtest.
    """
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT
              m.id AS match_id, m.played_at, m.team_a, m.team_b, m.best_of,
              m.team_a_score AS match_score_a, m.team_b_score AS match_score_b,
              mp.id AS map_id, mp.map_order, mp.map_name,
              mp.team_a_score AS map_score_a, mp.team_b_score AS map_score_b
            FROM matches m
            JOIN maps mp ON mp.match_id=m.id
            WHERE m.status='finished'
              AND m.played_at IS NOT NULL
              AND m.team_a_score IS NOT NULL
              AND m.team_b_score IS NOT NULL
              AND m.team_a_score <> m.team_b_score
              AND mp.map_name IS NOT NULL
              AND trim(mp.map_name) <> ''
              AND mp.team_a_score IS NOT NULL
              AND mp.team_b_score IS NOT NULL
              AND mp.team_a_score <> mp.team_b_score
            ORDER BY m.played_at ASC, m.id ASC, COALESCE(mp.map_order, mp.id) ASC
            """
        ).fetchall()

    grouped: dict[int, list] = {}
    order_ids: list[int] = []
    for row in rows:
        match_id = int(row["match_id"])
        if match_id not in grouped:
            grouped[match_id] = []
            order_ids.append(match_id)
        grouped[match_id].append(row)

    states: dict[str, _MapTeamState] = defaultdict(lambda: _MapTeamState(rating=initial_rating))
    output: list[MapFeatureRow] = []

    for match_id in order_ids:
        group = grouped[match_id]
        head = group[0]
        key_a = canonical_key(head["team_a"])
        key_b = canonical_key(head["team_b"])
        a = states[key_a]
        b = states[key_b]
        pre_general_rating_a = a.rating
        pre_general_rating_b = b.rating
        best_of = int(head["best_of"]) if head["best_of"] is not None else None

        # All target-map rows see exactly the same pre-series state.
        for map_row in group:
            map_name = canonical_key(map_row["map_name"])
            if not map_name:
                continue
            general_p, map_p, features, n_a, n_b = _features_for_map(
                a,
                b,
                map_name=map_name,
                map_order=int(map_row["map_order"]) if map_row["map_order"] is not None else None,
                best_of=best_of,
                initial_rating=initial_rating,
                elo_scale=elo_scale,
            )
            output.append(
                MapFeatureRow(
                    map_id=int(map_row["map_id"]),
                    match_id=match_id,
                    played_at=str(head["played_at"]),
                    team_a=str(head["team_a"]),
                    team_b=str(head["team_b"]),
                    best_of=best_of,
                    map_order=int(map_row["map_order"]) if map_row["map_order"] is not None else None,
                    map_name=map_name,
                    actual_a=int(int(map_row["map_score_a"]) > int(map_row["map_score_b"])),
                    general_elo_probability_a=general_p,
                    map_elo_probability_a=map_p,
                    same_map_history_a=n_a,
                    same_map_history_b=n_b,
                    features=features,
                )
            )

        # Update map states only after every row for this series has been emitted.
        for map_row in group:
            map_name = canonical_key(map_row["map_name"])
            if not map_name:
                continue
            actual_a = float(int(map_row["map_score_a"]) > int(map_row["map_score_b"]))
            actual_b = 1.0 - actual_a

            map_ra = a.map_ratings.get(map_name, initial_rating)
            map_rb = b.map_ratings.get(map_name, initial_rating)
            map_expected_a = expected_score(map_ra, map_rb, elo_scale)
            a.map_ratings[map_name] = map_ra + map_k_factor * (actual_a - map_expected_a)
            b.map_ratings[map_name] = map_rb + map_k_factor * (actual_b - (1.0 - map_expected_a))

            # Residual against general pre-series strength captures performance on
            # this map after accounting for opponent quality.
            general_expected_a = expected_score(pre_general_rating_a, pre_general_rating_b, elo_scale)
            _map_deque(a.map_results, map_name).append(actual_a)
            _map_deque(b.map_results, map_name).append(actual_b)
            _map_deque(a.map_opponent_ratings, map_name).append(pre_general_rating_b)
            _map_deque(b.map_opponent_ratings, map_name).append(pre_general_rating_a)
            _map_deque(a.map_residuals, map_name).append(actual_a - general_expected_a)
            _map_deque(b.map_residuals, map_name).append(actual_b - (1.0 - general_expected_a))
            a.recent_all_map_results.append(actual_a)
            b.recent_all_map_results.append(actual_b)

        actual_match_a = float(int(head["match_score_a"]) > int(head["match_score_b"]))
        expected_match_a = expected_score(pre_general_rating_a, pre_general_rating_b, elo_scale)
        a.rating = pre_general_rating_a + match_k_factor * (actual_match_a - expected_match_a)
        b.rating = pre_general_rating_b + match_k_factor * ((1.0 - actual_match_a) - (1.0 - expected_match_a))
        a.match_count += 1
        b.match_count += 1
        a.recent_match_results.append(actual_match_a)
        b.recent_match_results.append(1.0 - actual_match_a)
        a.recent_opponent_ratings.append(pre_general_rating_b)
        b.recent_opponent_ratings.append(pre_general_rating_a)

    return output


def _group_by_match(rows):
    groups: list[list] = []
    current_id = None
    current: list = []
    for row in rows:
        if current_id is None or row.match_id == current_id:
            current.append(row)
            current_id = row.match_id
            continue
        groups.append(current)
        current = [row]
        current_id = row.match_id
    if current:
        groups.append(current)
    return groups


def _feature_names(mode: str) -> tuple[str, ...]:
    if mode == "general":
        return GENERAL_FEATURES
    if mode == "map":
        return GENERAL_FEATURES + MAP_SPECIFIC_FEATURES
    raise ValueError("mode must be 'general' or 'map'")


def backtest_map_model(
    db_path: str | Path,
    *,
    mode: str = "map",
    min_train_maps: int = 300,
    retrain_every_matches: int = 25,
    c: float = 1.0,
) -> MapBacktestResult:
    """Strict expanding-window map-level logistic model grouped by series."""
    if min_train_maps < 20:
        raise ValueError("min_train_maps must be at least 20")
    if retrain_every_matches < 1:
        raise ValueError("retrain_every_matches must be at least 1")

    try:
        import numpy as np
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import StandardScaler
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError('Install model dependencies: pip install -e ".[model]"') from exc

    rows = build_map_feature_rows_impl(db_path)
    groups = _group_by_match(rows)
    names = _feature_names(mode)
    history: list[MapFeatureRow] = []
    predictions: list[MapPrediction] = []
    model = None
    matches_since_fit = 0

    for group in groups:
        if len(history) >= min_train_maps and len({row.actual_a for row in history}) >= 2:
            if model is None or matches_since_fit >= retrain_every_matches:
                x_train = np.asarray(
                    [[row.features[name] for name in names] for row in history],
                    dtype=float,
                )
                y_train = np.asarray([row.actual_a for row in history], dtype=int)
                model = Pipeline(
                    [
                        ("scale", StandardScaler()),
                        (
                            "logit",
                            LogisticRegression(
                                C=c,
                                max_iter=2000,
                                solver="lbfgs",
                                random_state=42,
                            ),
                        ),
                    ]
                )
                model.fit(x_train, y_train)
                matches_since_fit = 0

            x_group = np.asarray(
                [[row.features[name] for name in names] for row in group],
                dtype=float,
            )
            probabilities = model.predict_proba(x_group)[:, 1]
            train_size = len(history)
            for row, probability in zip(group, probabilities):
                predictions.append(
                    MapPrediction(
                        map_id=row.map_id,
                        match_id=row.match_id,
                        played_at=row.played_at,
                        team_a=row.team_a,
                        team_b=row.team_b,
                        map_name=row.map_name,
                        map_order=row.map_order,
                        actual_a=row.actual_a,
                        probability_a=float(probability),
                        general_elo_probability_a=row.general_elo_probability_a,
                        map_elo_probability_a=row.map_elo_probability_a,
                        train_maps=train_size,
                        same_map_history_a=row.same_map_history_a,
                        same_map_history_b=row.same_map_history_b,
                    )
                )
            matches_since_fit += 1

        history.extend(group)

    actuals = [prediction.actual_a for prediction in predictions]
    return MapBacktestResult(
        feature_rows=len(rows),
        evaluated_maps=len(predictions),
        evaluated_matches=len({prediction.match_id for prediction in predictions}),
        feature_names=names,
        predictions=predictions,
        raw_metrics=probability_metrics([prediction.probability_a for prediction in predictions], actuals),
        general_elo_metrics=probability_metrics(
            [prediction.general_elo_probability_a for prediction in predictions], actuals
        ),
        map_elo_metrics=probability_metrics(
            [prediction.map_elo_probability_a for prediction in predictions], actuals
        ),
    )
