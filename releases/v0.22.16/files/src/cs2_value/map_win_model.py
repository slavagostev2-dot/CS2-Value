from __future__ import annotations

import argparse
import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median

from .db import connect
from .elo import expected_score
from .identity import canonical_key
from .metrics import ProbabilityMetrics, probability_metrics


GENERAL_FEATURES = (
    "general_elo_logit_diff",
    "match_form_diff",
    "match_sos_diff",
    "experience_diff",
    "overall_map_rate_diff",
    "overall_map_experience_diff",
    "bo1",
    "bo3",
    "bo5",
    "map_order_1",
    "map_order_2",
    "map_order_3",
    "map_order_4",
    "map_order_5",
)

MAP_SPECIFIC_FEATURES = (
    "same_map_rate_diff",
    "same_map_elo_logit_diff",
    "same_map_sos_diff",
    "same_map_residual_diff",
    "same_map_experience_diff",
    "same_map_shared_experience",
    "same_map_min_history",
    "same_map_known_both",
)


@dataclass(frozen=True)
class MapFeatureRow:
    map_id: int
    match_id: int
    played_at: str
    team_a: str
    team_b: str
    best_of: int | None
    map_order: int | None
    map_name: str
    actual_a: int
    general_elo_probability_a: float
    map_elo_probability_a: float
    same_map_history_a: int
    same_map_history_b: int
    features: dict[str, float]

    @property
    def min_same_map_history(self) -> int:
        return min(self.same_map_history_a, self.same_map_history_b)


@dataclass(frozen=True)
class MapPrediction:
    map_id: int
    match_id: int
    played_at: str
    team_a: str
    team_b: str
    map_name: str
    map_order: int | None
    actual_a: int
    probability_a: float
    general_elo_probability_a: float
    map_elo_probability_a: float
    train_maps: int
    same_map_history_a: int
    same_map_history_b: int

    @property
    def min_same_map_history(self) -> int:
        return min(self.same_map_history_a, self.same_map_history_b)


@dataclass(frozen=True)
class CalibratedMapPrediction:
    map_id: int
    match_id: int
    played_at: str
    team_a: str
    team_b: str
    map_name: str
    map_order: int | None
    actual_a: int
    raw_probability_a: float
    platt_probability_a: float
    isotonic_probability_a: float
    general_elo_probability_a: float
    map_elo_probability_a: float
    same_map_history_a: int
    same_map_history_b: int

    @property
    def min_same_map_history(self) -> int:
        return min(self.same_map_history_a, self.same_map_history_b)


@dataclass(frozen=True)
class MapCoverage:
    usable_finished_matches: int
    matches_with_maps: int
    valid_maps: int
    distinct_maps: int
    median_maps_per_match: float

    @property
    def match_percentage(self) -> float:
        return (
            self.matches_with_maps / self.usable_finished_matches * 100.0
            if self.usable_finished_matches
            else 0.0
        )


@dataclass
class MapBacktestResult:
    feature_rows: int
    evaluated_maps: int
    evaluated_matches: int
    feature_names: tuple[str, ...]
    predictions: list[MapPrediction] = field(default_factory=list)
    raw_metrics: ProbabilityMetrics | None = None
    general_elo_metrics: ProbabilityMetrics | None = None
    map_elo_metrics: ProbabilityMetrics | None = None


@dataclass
class MapCalibrationResult:
    source_predictions: int
    evaluated_maps: int
    evaluated_matches: int
    raw_metrics: ProbabilityMetrics
    platt_metrics: ProbabilityMetrics
    isotonic_metrics: ProbabilityMetrics
    general_elo_metrics: ProbabilityMetrics
    map_elo_metrics: ProbabilityMetrics
    neutral_metrics: ProbabilityMetrics
    predictions: list[CalibratedMapPrediction] = field(default_factory=list)


@dataclass(frozen=True)
class MapHistoryBand:
    label: str
    maps: int
    platt_metrics: ProbabilityMetrics


@dataclass
class MapWinUpgradeReport:
    coverage: MapCoverage
    general: MapCalibrationResult
    map_specific: MapCalibrationResult
    accepted: bool
    reason: str
    bands: list[MapHistoryBand]


@dataclass
class _MapTeamState:
    rating: float = 1500.0
    match_count: int = 0
    recent_match_results: deque[float] = field(default_factory=lambda: deque(maxlen=12))
    recent_opponent_ratings: deque[float] = field(default_factory=lambda: deque(maxlen=12))
    recent_all_map_results: deque[float] = field(default_factory=lambda: deque(maxlen=50))
    map_results: dict[str, deque[float]] = field(default_factory=dict)
    map_opponent_ratings: dict[str, deque[float]] = field(default_factory=dict)
    map_residuals: dict[str, deque[float]] = field(default_factory=dict)
    map_ratings: dict[str, float] = field(default_factory=dict)


def _weighted_mean(values: deque[float], *, decay: float, default: float) -> float:
    if not values:
        return default
    vals = list(values)
    weights = [decay ** (len(vals) - 1 - index) for index in range(len(vals))]
    total = sum(weights)
    return sum(value * weight for value, weight in zip(vals, weights)) / total


def _mean(values: deque[float], default: float) -> float:
    return sum(values) / len(values) if values else default


def _smoothed_recent_rate(
    values: deque[float],
    *,
    prior_n: float = 8.0,
    decay: float = 0.92,
) -> float:
    if not values:
        return 0.5
    recent = _weighted_mean(values, decay=decay, default=0.5)
    reliability = len(values) / (len(values) + prior_n)
    return 0.5 + reliability * (recent - 0.5)


def _overall_map_rate(values: deque[float]) -> float:
    if not values:
        return 0.5
    return (sum(values) + 6.0 * 0.5) / (len(values) + 6.0)


def _map_deque(mapping: dict[str, deque[float]], map_name: str, maxlen: int = 30) -> deque[float]:
    return mapping.setdefault(map_name, deque(maxlen=maxlen))


def _state_features(state: _MapTeamState, map_name: str, *, initial_rating: float) -> dict[str, float]:
    map_results = state.map_results.get(map_name, deque())
    map_sos = state.map_opponent_ratings.get(map_name, deque())
    map_residual = state.map_residuals.get(map_name, deque())
    return {
        "rating": state.rating,
        "match_form": _weighted_mean(state.recent_match_results, decay=0.86, default=0.5),
        "match_sos": _mean(state.recent_opponent_ratings, initial_rating),
        "experience": math.log1p(state.match_count),
        "overall_map_rate": _overall_map_rate(state.recent_all_map_results),
        "overall_map_experience": math.log1p(len(state.recent_all_map_results)),
        "same_map_rate": _smoothed_recent_rate(map_results),
        "same_map_sos": _mean(map_sos, initial_rating),
        "same_map_residual": _weighted_mean(map_residual, decay=0.92, default=0.0),
        "same_map_experience": math.log1p(len(map_results)),
        "same_map_count": float(len(map_results)),
        "same_map_rating": state.map_ratings.get(map_name, initial_rating),
    }


def _features_for_map(
    a: _MapTeamState,
    b: _MapTeamState,
    *,
    map_name: str,
    map_order: int | None,
    best_of: int | None,
    initial_rating: float,
    elo_scale: float,
) -> tuple[float, float, dict[str, float], int, int]:
    fa = _state_features(a, map_name, initial_rating=initial_rating)
    fb = _state_features(b, map_name, initial_rating=initial_rating)
    n_a = int(fa["same_map_count"])
    n_b = int(fb["same_map_count"])

    general_p = expected_score(a.rating, b.rating, elo_scale)
    map_p = expected_score(fa["same_map_rating"], fb["same_map_rating"], elo_scale)
    order = int(map_order or 0)
    features = {
        "general_elo_logit_diff": (a.rating - b.rating) / elo_scale,
        "match_form_diff": fa["match_form"] - fb["match_form"],
        "match_sos_diff": (fa["match_sos"] - fb["match_sos"]) / elo_scale,
        "experience_diff": fa["experience"] - fb["experience"],
        "overall_map_rate_diff": fa["overall_map_rate"] - fb["overall_map_rate"],
        "overall_map_experience_diff": fa["overall_map_experience"] - fb["overall_map_experience"],
        "bo1": 1.0 if best_of == 1 else 0.0,
        "bo3": 1.0 if best_of == 3 else 0.0,
        "bo5": 1.0 if best_of == 5 else 0.0,
        "map_order_1": 1.0 if order == 1 else 0.0,
        "map_order_2": 1.0 if order == 2 else 0.0,
        "map_order_3": 1.0 if order == 3 else 0.0,
        "map_order_4": 1.0 if order == 4 else 0.0,
        "map_order_5": 1.0 if order == 5 else 0.0,
        "same_map_rate_diff": fa["same_map_rate"] - fb["same_map_rate"],
        "same_map_elo_logit_diff": (fa["same_map_rating"] - fb["same_map_rating"]) / elo_scale,
        "same_map_sos_diff": (fa["same_map_sos"] - fb["same_map_sos"]) / elo_scale,
        "same_map_residual_diff": fa["same_map_residual"] - fb["same_map_residual"],
        "same_map_experience_diff": fa["same_map_experience"] - fb["same_map_experience"],
        "same_map_shared_experience": math.log1p(min(n_a, n_b)),
        "same_map_min_history": min(n_a, n_b) / (min(n_a, n_b) + 8.0) if min(n_a, n_b) else 0.0,
        "same_map_known_both": 1.0 if n_a > 0 and n_b > 0 else 0.0,
    }
    return general_p, map_p, features, n_a, n_b


def build_map_feature_rows(
    db_path: str | Path,
    *,
    initial_rating: float = 1500.0,
    match_k_factor: float = 24.0,
    map_k_factor: float = 18.0,
    elo_scale: float = 400.0,
) ...