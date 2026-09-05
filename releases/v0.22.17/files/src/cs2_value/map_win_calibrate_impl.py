from __future__ import annotations

import math
from pathlib import Path
from statistics import median

from .db import connect
from .metrics import probability_metrics
from .map_win_model import (
    CalibratedMapPrediction, MapCalibrationResult, MapCoverage, MapHistoryBand,
    MapPrediction, MapWinUpgradeReport,
)
from .map_win_build_impl import _group_by_match, backtest_map_model

def _clip_probability(value: float) -> float:
    return min(max(float(value), 1e-6), 1.0 - 1e-6)


def _logit(value: float) -> float:
    p = _clip_probability(value)
    return math.log(p / (1.0 - p))


def calibrate_map_predictions(
    source: list[MapPrediction],
    *,
    min_train_predictions: int = 500,
    retrain_every_matches: int = 40,
) -> MapCalibrationResult:
    """Strict walk-forward Platt/isotonic calibration grouped by match."""
    if min_train_predictions < 30:
        raise ValueError("min_train_predictions must be at least 30")
    if retrain_every_matches < 1:
        raise ValueError("retrain_every_matches must be at least 1")

    try:
        import numpy as np
        from sklearn.isotonic import IsotonicRegression
        from sklearn.linear_model import LogisticRegression
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError('Install model dependencies: pip install -e ".[model]"') from exc

    groups = _group_by_match(source)
    history: list[MapPrediction] = []
    output: list[CalibratedMapPrediction] = []
    platt = None
    isotonic = None
    matches_since_fit = 0

    for group in groups:
        if len(history) >= min_train_predictions and len({item.actual_a for item in history}) >= 2:
            if platt is None or isotonic is None or matches_since_fit >= retrain_every_matches:
                y_train = np.asarray([item.actual_a for item in history], dtype=int)
                x_platt = np.asarray([[_logit(item.probability_a)] for item in history], dtype=float)
                platt = LogisticRegression(C=1000.0, max_iter=2000, solver="lbfgs", random_state=42)
                platt.fit(x_platt, y_train)
                isotonic = IsotonicRegression(out_of_bounds="clip")
                isotonic.fit(
                    np.asarray([item.probability_a for item in history], dtype=float),
                    y_train.astype(float),
                )
                matches_since_fit = 0

            block_x = np.asarray([[_logit(item.probability_a)] for item in group], dtype=float)
            platt_probs = [float(value) for value in platt.predict_proba(block_x)[:, 1]]
            iso_probs = [
                float(value)
                for value in isotonic.predict(
                    np.asarray([item.probability_a for item in group], dtype=float)
                )
            ]
            for item, p_platt, p_iso in zip(group, platt_probs, iso_probs):
                output.append(
                    CalibratedMapPrediction(
                        map_id=item.map_id,
                        match_id=item.match_id,
                        played_at=item.played_at,
                        team_a=item.team_a,
                        team_b=item.team_b,
                        map_name=item.map_name,
                        map_order=item.map_order,
                        actual_a=item.actual_a,
                        raw_probability_a=item.probability_a,
                        platt_probability_a=_clip_probability(p_platt),
                        isotonic_probability_a=_clip_probability(p_iso),
                        general_elo_probability_a=item.general_elo_probability_a,
                        map_elo_probability_a=item.map_elo_probability_a,
                        same_map_history_a=item.same_map_history_a,
                        same_map_history_b=item.same_map_history_b,
                    )
                )
            matches_since_fit += 1

        history.extend(group)

    actuals = [item.actual_a for item in output]
    return MapCalibrationResult(
        source_predictions=len(source),
        evaluated_maps=len(output),
        evaluated_matches=len({item.match_id for item in output}),
        raw_metrics=probability_metrics([item.raw_probability_a for item in output], actuals),
        platt_metrics=probability_metrics([item.platt_probability_a for item in output], actuals),
        isotonic_metrics=probability_metrics([item.isotonic_probability_a for item in output], actuals),
        general_elo_metrics=probability_metrics(
            [item.general_elo_probability_a for item in output], actuals
        ),
        map_elo_metrics=probability_metrics([item.map_elo_probability_a for item in output], actuals),
        neutral_metrics=probability_metrics([0.5] * len(output), actuals),
        predictions=output,
    )


def map_coverage(db_path: str | Path) -> MapCoverage:
    with connect(db_path) as conn:
        usable = int(
            conn.execute(
                """
                SELECT COUNT(*) AS n FROM matches
                WHERE status='finished' AND played_at IS NOT NULL
                  AND team_a_score IS NOT NULL AND team_b_score IS NOT NULL
                  AND team_a_score <> team_b_score
                """
            ).fetchone()["n"]
            or 0
        )
        rows = conn.execute(
            """
            SELECT m.id AS match_id, COUNT(*) AS maps
            FROM matches m
            JOIN maps mp ON mp.match_id=m.id
            WHERE m.status='finished' AND m.played_at IS NOT NULL
              AND m.team_a_score IS NOT NULL AND m.team_b_score IS NOT NULL
              AND m.team_a_score <> m.team_b_score
              AND trim(mp.map_name) <> ''
              AND mp.team_a_score <> mp.team_b_score
            GROUP BY m.id
            """
        ).fetchall()
        distinct_maps = int(
            conn.execute(
                "SELECT COUNT(DISTINCT lower(trim(map_name))) AS n FROM maps WHERE trim(map_name) <> ''"
            ).fetchone()["n"]
            or 0
        )
    map_counts = [int(row["maps"]) for row in rows]
    return MapCoverage(
        usable_finished_matches=usable,
        matches_with_maps=len(rows),
        valid_maps=sum(map_counts),
        distinct_maps=distinct_maps,
        median_maps_per_match=float(median(map_counts)) if map_counts else 0.0,
    )


def compare_map_win_upgrade(
    db_path: str | Path,
    *,
    model_min_train_maps: int = 300,
    model_retrain_every_matches: int = 25,
    calibration_min_train_predictions: int = 500,
    calibration_retrain_every_matches: int = 40,
) -> MapWinUpgradeReport:
    common = dict(
        min_train_maps=model_min_train_maps,
        retrain_every_matches=model_retrain_every_matches,
    )
    general_raw = backtest_map_model(db_path, mode="general", **common)
    map_raw = backtest_map_model(db_path, mode="map", **common)
    general = calibrate_map_predictions(
        general_raw.predictions,
        min_train_predictions=calibration_min_train_predictions,
        retrain_every_matches=calibration_retrain_every_matches,
    )
    map_specific = calibrate_map_predictions(
        map_raw.predictions,
        min_train_predictions=calibration_min_train_predictions,
        retrain_every_matches=calibration_retrain_every_matches,
    )

    gm = general.platt_metrics
    mm = map_specific.platt_metrics
    accepted = bool(
        gm.brier_score is not None
        and gm.log_loss is not None
        and mm.brier_score is not None
        and mm.log_loss is not None
        and mm.brier_score < gm.brier_score
        and mm.log_loss < gm.log_loss
    )
    if accepted:
        reason = (
            "Map-specific block улучшил одновременно Brier и Log Loss относительно "
            "той же map-level модели без статистики конкретной карты. Блок принимаем "
            "кандидатом для series model; ROI проверим только после сборки вероятности BO3."
        )
    else:
        reason = (
            "Map-specific block не улучшил одновременно Brier и Log Loss относительно "
            "общей map-level модели. По правилу проекта пока не принимаем его и не "
            "подгоняем по ROI."
        )

    band_specs = (
        ("0–2 прошлых карт у одной из команд", lambda n: n <= 2),
        ("минимум 3, но меньше 6", lambda n: 3 <= n <= 5),
        ("минимум 6, но меньше 10", lambda n: 6 <= n <= 9),
        ("у обеих минимум 10 на этой карте", lambda n: n >= 10),
    )
    bands: list[MapHistoryBand] = []
    for label, predicate in band_specs:
        subset = [item for item in map_specific.predictions if predicate(item.min_same_map_history)]
        bands.append(
            MapHistoryBand(
                label=label,
                maps=len(subset),
                platt_metrics=probability_metrics(
                    [item.platt_probability_a for item in subset],
                    [item.actual_a for item in subset],
                ),
            )
        )

    return MapWinUpgradeReport(
        coverage=map_coverage(db_path),
        general=general,
        map_specific=map_specific,
        accepted=accepted,
        reason=reason,
        bands=bands,
    )
