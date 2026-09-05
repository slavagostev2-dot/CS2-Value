from cs2_value.offstage_dom_veto import veto_from_dom_payload


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
