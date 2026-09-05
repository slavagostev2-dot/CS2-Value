from cs2_value.models import VetoAction
from cs2_value.offstage_dom_veto import merge_dom_veto_with_text, veto_from_dom_payload


def _bo3_text(map1="Anubis", map2="Mirage", decider="Cache"):
    return (
        VetoAction(map1, "pick", None, 1, 1),
        VetoAction(map2, "pick", None, 2, 2),
        VetoAction(decider, "decider", None, 3, 3),
        VetoAction("Ancient", "ban", None, 4, None),
        VetoAction("Nuke", "ban", None, 5, None),
        VetoAction("Dust2", "ban", None, 6, None),
        VetoAction("Inferno", "ban", None, 7, None),
    )


def test_pick_badges_not_large_map_winner_logo_are_authoritative():
    payload = {
        "team_assets": {"a": ["img:falcons"], "b": ["img:misa"]},
        "maps": [
            {"map": "Anubis", "order": 1, "score": 100, "assets": ["img:misa"]},
            {"map": "Mirage", "order": 2, "score": 100, "assets": ["img:misa"]},
            {"map": "Cache", "order": 3, "score": 100, "assets": []},
        ],
        "pick_badges": [
            {"map": "Anubis", "order": 10, "score": 175, "assets": ["img:falcons"]},
            {"map": "Mirage", "order": 11, "score": 175, "assets": ["img:misa"]},
        ],
    }
    veto = veto_from_dom_payload(
        payload, "Falcons Force", "Misa", 3, text_veto=_bo3_text()
    )
    assert [(v.map_name, v.action, v.team_name) for v in veto] == [
        ("Anubis", "pick", "Falcons Force"),
        ("Mirage", "pick", "Misa"),
        ("Cache", "decider", None),
    ]


def test_real_falcons_misa_shape_uses_only_pick_badge_then_safe_bo3_complement():
    payload = {
        "team_assets": {"a": ["img:falcons"], "b": ["img:misa"]},
        "maps": [
            {"map": name, "order": i, "score": 100, "assets": []}
            for i, name in enumerate(
                ("Ancient", "Nuke", "Dust2", "Inferno", "Anubis", "Mirage", "Cache"), 1
            )
        ],
        "pick_badges": [
            {"map": "Anubis", "order": 20, "score": 175, "assets": ["img:misa"]},
            {"map": "Mirage", "order": 21, "score": 175, "assets": ["svgpath:falcons-badge-variant"]},
        ],
    }
    veto = veto_from_dom_payload(
        payload, "Falcons Force", "Misa", 3, text_veto=_bo3_text()
    )
    assert [(v.map_name, v.action, v.team_name) for v in veto] == [
        ("Anubis", "pick", "Misa"),
        ("Mirage", "pick", "Falcons Force"),
        ("Cache", "decider", None),
    ]


def test_bo3_complement_requires_both_pick_badges_to_exist():
    payload = {
        "team_assets": {"a": ["a"], "b": ["b"]},
        "pick_badges": [
            {"map": "Anubis", "order": 1, "score": 100, "assets": ["b"]},
        ],
    }
    assert veto_from_dom_payload(
        payload, "Falcons Force", "Misa", 3, text_veto=_bo3_text()
    ) == ()


def test_bo3_refuses_when_neither_pick_badge_matches_a_team():
    payload = {
        "team_assets": {"a": ["a"], "b": ["b"]},
        "pick_badges": [
            {"map": "Anubis", "order": 1, "score": 100, "assets": ["x"]},
            {"map": "Mirage", "order": 2, "score": 100, "assets": ["y"]},
        ],
    }
    assert veto_from_dom_payload(payload, "A", "B", 3, text_veto=_bo3_text()) == ()


def test_bo5_never_uses_single_picker_complement_inference():
    text = tuple(
        VetoAction(name, "decider" if i == 5 else "pick", None, i, i)
        for i, name in enumerate(("Anubis", "Mirage", "Ancient", "Nuke", "Cache"), 1)
    )
    payload = {
        "team_assets": {"a": ["a"], "b": ["b"]},
        "pick_badges": [
            {"map": "Anubis", "order": 1, "score": 100, "assets": ["a"]},
            {"map": "Mirage", "order": 2, "score": 100, "assets": ["x"]},
            {"map": "Ancient", "order": 3, "score": 100, "assets": ["y"]},
            {"map": "Nuke", "order": 4, "score": 100, "assets": ["z"]},
        ],
    }
    assert veto_from_dom_payload(payload, "A", "B", 5, text_veto=text) == ()


def test_dom_picks_override_text_picks_but_text_bans_are_retained():
    dom = (
        VetoAction("Anubis", "pick", "Falcons Force", 1, 1),
        VetoAction("Mirage", "pick", "Misa", 2, 2),
        VetoAction("Cache", "decider", None, 3, 3),
    )
    text = _bo3_text()
    merged = merge_dom_veto_with_text(dom, text)
    assert [(v.map_name, v.action, v.team_name, v.action_order, v.series_map_order) for v in merged] == [
        ("Anubis", "pick", "Falcons Force", 1, 1),
        ("Mirage", "pick", "Misa", 2, 2),
        ("Cache", "decider", None, 3, 3),
        ("Ancient", "ban", None, 4, None),
        ("Nuke", "ban", None, 5, None),
        ("Dust2", "ban", None, 6, None),
        ("Inferno", "ban", None, 7, None),
    ]
