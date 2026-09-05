from cs2_value.map_win_model import (
    backtest_map_model,
    build_map_feature_rows,
    compare_map_win_upgrade,
    series_win_probability,
)


def test_map_win_model_release_imports_and_bo3_formula():
    assert callable(build_map_feature_rows)
    assert callable(backtest_map_model)
    assert callable(compare_map_win_upgrade)
    p1, p2, p3 = 0.60, 0.40, 0.55
    expected = p1 * p2 + p1 * (1 - p2) * p3 + (1 - p1) * p2 * p3
    assert abs(series_win_probability([p1, p2, p3]) - expected) < 1e-12
