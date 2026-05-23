"""Parser for the trusted analyst's weekly levels table."""
from app.analysis.analyst_levels import parse_levels_table


def test_parses_standard_rows():
    rows = parse_levels_table(
        "CURITY CLB36+B1:C WEEKLY CPL WEEKLY SUPPORT/RESISTANCE LEVELS\n"
        "NVDA 215.24 220 240.4 235.2 225.2 / 210 204.8 194.8\n"
    )
    assert len(rows) == 1
    r = rows[0]
    assert r["ticker"] == "NVDA"
    assert r["clb36"] == 215.24
    assert r["weekly_cpl"] == 220.0
    assert r["resistances"] == [240.4, 235.2, 225.2]
    assert r["supports"] == [210.0, 204.8, 194.8]


def test_handles_multiword_and_symbol_tickers():
    rows = parse_levels_table(
        "BRK B 486.4 483.7 505.9 497.6 492 / 478.1 469.9 464.3\n"
        "ES_F 7492 7348 7626.2 7559.1 / 7389.6 7287.2\n"
    )
    by_ticker = {r["ticker"]: r for r in rows}
    assert "BRK B" in by_ticker
    assert by_ticker["BRK B"]["resistances"] == [505.9, 497.6, 492.0]
    assert by_ticker["ES_F"]["supports"] == [7389.6, 7287.2]


def test_skips_blank_and_unparseable_lines():
    rows = parse_levels_table("\n\nnonsense header line\nAAPL 308.8 305 331.7 / 298.7\n")
    assert len(rows) == 1
    assert rows[0]["ticker"] == "AAPL"


def test_levels_are_sorted_high_to_low():
    rows = parse_levels_table("SPY 745 742 752.5 770 759.4 / 717.7 735.1 724.6\n")
    assert rows[0]["resistances"] == sorted(rows[0]["resistances"], reverse=True)
    assert rows[0]["supports"] == sorted(rows[0]["supports"], reverse=True)
