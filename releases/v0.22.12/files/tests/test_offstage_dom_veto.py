from cs2_value.models import VetoAction
from cs2_value.offstage_dom_veto import merge_dom_veto_with_text, veto_from_dom_payload


def test_live_fnatic_bbl_scoreboard_logo_picks_and_decider():
    payload = {
        "team_assets": {
            "a": ["svgpath:fnatic-logo"],
            "b": ["svgpath:bbl-logo"],
        },
        "maps": [
            {"map": "Cache", "order": 10, "score": 165, "assets": ["svgpath:fnatic-logo"]},
            {"map": "Cache", "order": 40, "score": 55, "assets": ["bg:cache.jpg"]},
            {"map": "Inferno", "order": 11, "score": 165, "assets": ["svgpath:bbl-logo"]},
            {"map": "Ancient", "order": 12, "score": 155, "assets": []},
        ],
    }
    veto = veto_from_dom_payload(payload, "fnatic", "BBL Esports", 3)
    assert [(v.map_name, v.action, v.team_name, v.series_map_order) for v in veto] == [
        ("Cache", "pick", "fnatic", 1),
        ("Inferno", "pick", "BBL Esports", 2),
        ("Ancient", "decider", None, 3),
    ]


def test_dom_veto_refuses_to_guess_without_two_picker_logos():
    payload = {
        "team_assets": {"a": ["a"], "b": ["b"]},
        "maps": [
            {"map": "Cache", "order": 1, "score": 100, "assets": ["a"]},
            {"map": "Inferno", "order": 2, "score": 100, "assets": []},
            {"map": "Ancient", "order": 3, "score": 100, "assets": []},
        ],
    }
    assert veto_from_dom_payload(payload, "fnatic", "BBL Esports", 3) == ()


def test_dom_picks_override_text_picks_but_text_bans_are_retained():
    dom = (
        VetoAction("Anubis", "pick", "Falcons Force", 1, 1),
        VetoAction("Mirage", "pick", "Misa", 2, 2),
        VetoAction("Cache", "decider", None, 3, 3),
    )
    text = (
        VetoAction("Anubis", "pick", None, 1, 1),
        VetoAction("Mirage", "pick", None, 2, 2),
        VetoAction("Cache", "decider", None, 3, 3),
        VetoAction("Ancient", "ban", None, 4, None),
        VetoAction("Nuke", "ban", None, 5, None),
        VetoAction("Dust2", "ban", None, 6, None),
        VetoAction("Inferno", "ban", None, 7, None),
    )
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
