from __future__ import annotations

import argparse
from collections import defaultdict

from .metrics import ProbabilityMetrics
from .map_win_model import MapWinUpgradeReport
from .map_win_calibrate_impl import _clip_probability, compare_map_win_upgrade

def series_win_probability(map_probabilities_a: list[float] | tuple[float, ...]) -> float:
    """Combine ordered per-map probabilities into a BO-series win probability."""
    probabilities = [_clip_probability(value) for value in map_probabilities_a]
    if not probabilities or len(probabilities) % 2 == 0:
        raise ValueError("series requires an odd number of map probabilities")
    wins_needed = len(probabilities) // 2 + 1
    states: dict[tuple[int, int], float] = {(0, 0): 1.0}
    for probability in probabilities:
        nxt: dict[tuple[int, int], float] = defaultdict(float)
        for (wins_a, wins_b), mass in states.items():
            if wins_a >= wins_needed or wins_b >= wins_needed:
                nxt[(wins_a, wins_b)] += mass
                continue
            nxt[(wins_a + 1, wins_b)] += mass * probability
            nxt[(wins_a, wins_b + 1)] += mass * (1.0 - probability)
        states = dict(nxt)
    return sum(mass for (wins_a, _wins_b), mass in states.items() if wins_a >= wins_needed)


def _fmt_metric(m: ProbabilityMetrics) -> str:
    if not m.n or m.brier_score is None or m.log_loss is None:
        return "нет данных"
    accuracy = m.accuracy * 100.0 if m.accuracy is not None else 0.0
    return f"winner {accuracy:.1f}% | Brier {m.brier_score:.4f} | Log Loss {m.log_loss:.4f}"


def print_map_win_report_ru(report: MapWinUpgradeReport) -> None:
    print("=== MAP WIN MODEL V1: ЧЕСТНЫЙ MAP-LEVEL WALK-FORWARD ===")
    print(
        "Цель: вероятность победы команды на КОНКРЕТНОЙ карте. Для каждой серии все карты "
        "получают признаки из состояния ДО начала матча; результат первой карты не может попасть во вторую."
    )
    print(
        "Важно: исторический тест условный по фактически сыгранной карте/порядку. Он НЕ доказывает, "
        "что старый veto был доступен prematch. Реальный timestamped veto используется только вперёд."
    )
    print()
    c = report.coverage
    print(
        f"Покрытие: {c.matches_with_maps}/{c.usable_finished_matches} завершённых матчей "
        f"({c.match_percentage:.1f}%) имеют пригодные карты; валидных карт: {c.valid_maps}; "
        f"названий карт: {c.distinct_maps}; медиана карт/матч: {c.median_maps_per_match:.1f}."
    )
    print(
        f"Строго проверено после model+calibration warm-up: general={report.general.evaluated_maps} карт "
        f"в {report.general.evaluated_matches} матчах; map-specific={report.map_specific.evaluated_maps} карт "
        f"в {report.map_specific.evaluated_matches} матчах."
    )
    print()
    print("Без статистики конкретной карты (general map-level):")
    print("  RAW:   " + _fmt_metric(report.general.raw_metrics))
    print("  Platt: " + _fmt_metric(report.general.platt_metrics))
    print()
    print("+ статистика именно этой карты (winrate shrinkage + sample + map Elo + SoS + residual):")
    print("  RAW:   " + _fmt_metric(report.map_specific.raw_metrics))
    print("  Platt: " + _fmt_metric(report.map_specific.platt_metrics))
    print("  Map Elo alone на том же окне: " + _fmt_metric(report.map_specific.map_elo_metrics))
    print("  General Elo на том же окне:   " + _fmt_metric(report.map_specific.general_elo_metrics))
    print("  Всегда 50/50:                  " + _fmt_metric(report.map_specific.neutral_metrics))

    gm, mm = report.general.platt_metrics, report.map_specific.platt_metrics
    if None not in (gm.brier_score, gm.log_loss, mm.brier_score, mm.log_loss):
        print(
            f"  Δ Brier map-specific vs general: {mm.brier_score - gm.brier_score:+.4f} "
            "(отрицательное лучше)"
        )
        print(
            f"  Δ Log Loss map-specific vs general: {mm.log_loss - gm.log_loss:+.4f} "
            "(отрицательное лучше)"
        )
    print()
    print("Качество по минимальной истории именно этой карты у двух команд:")
    for band in report.bands:
        print(f"  {band.label:<38} {band.maps:5d} карт | {_fmt_metric(band.platt_metrics)}")
    print()
    print("Решение:")
    print("  " + ("MAP-SPECIFIC БЛОК ПРИНЯТ КАК КАНДИДАТ" if report.accepted else "MAP-SPECIFIC БЛОК ПОКА НЕ ПРИНЯТ"))
    print("  " + report.reason)
    print()
    print(
        "ROI здесь намеренно не выбирает признаки. Если map-specific проходит Brier+Log Loss, "
        "следующий шаг — применить его к реальным PICK/DECIDER и собрать вероятность BO3, "
        "после чего повторить исторический/forward VALUE-тест."
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m cs2_value.map_win_model")
    parser.add_argument("--db", default="data/cs2_value.db")
    parser.add_argument("--model-min-train-maps", type=int, default=300)
    parser.add_argument("--model-retrain-every-matches", type=int, default=25)
    parser.add_argument("--calibration-min-train-predictions", type=int, default=500)
    parser.add_argument("--calibration-retrain-every-matches", type=int, default=40)
    args = parser.parse_args()
    report = compare_map_win_upgrade(
        args.db,
        model_min_train_maps=args.model_min_train_maps,
        model_retrain_every_matches=args.model_retrain_every_matches,
        calibration_min_train_predictions=args.calibration_min_train_predictions,
        calibration_retrain_every_matches=args.calibration_retrain_every_matches,
    )
    print_map_win_report_ru(report)
