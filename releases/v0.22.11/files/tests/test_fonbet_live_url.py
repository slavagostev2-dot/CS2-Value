from cs2_value.bookmakers.fonbet import FonbetAdapter


def test_fonbet_live_esports_event_url_from_user():
    ref = FonbetAdapter.parse_event_url(
        "https://fon.bet/live/esports/81305/67730260 "
    )
    assert ref.tournament_id == "81305"
    assert ref.event_id == "67730260"
    assert ref.url == "https://fon.bet/live/esports/81305/67730260"


def test_fonbet_live_category_cs_event_url():
    ref = FonbetAdapter.parse_event_url(
        "https://fon.bet/live/esports/category/cs/81305/67730260"
    )
    assert ref.tournament_id == "81305"
    assert ref.event_id == "67730260"
