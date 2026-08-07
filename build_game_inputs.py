"""
Turns raw data from data_fetch.py into the GameInputs objects model.py expects.
This is the "glue" layer -- if a stat source's column names drift, this is
where you'll patch it, not in model.py itself.
"""

import datetime as dt
from model import (
    TeamOffense, HandednessSplit, StarterPitcher, Bullpen,
    ParkWeather, MarketLines, TeamInputs, GameInputs, SharpMoneySplits,
)
from parks import PARKS
import data_fetch as df


def trailing_30_window(as_of: dt.date) -> tuple[str, str]:
    start = as_of - dt.timedelta(days=30)
    return start.isoformat(), as_of.isoformat()


def find_team_row(team_df, team_abbrev: str):
    """FanGraphs team tables use full names or their own abbreviations,
    which don't always match MLB Stats API abbreviations 1:1 (e.g. CHW vs CWS).
    Keep an explicit mapping here if pybaseball's team column doesn't line up."""
    ABBREV_FIX = {"CHW": "CHW", "CWS": "CHW", "WSN": "WSN", "WSH": "WSN", "KCR": "KCR", "KC": "KCR",
                  "SDP": "SDP", "SD": "SDP", "SFG": "SFG", "SF": "SFG", "TBR": "TBR", "TB": "TBR"}
    target = ABBREV_FIX.get(team_abbrev, team_abbrev)
    matches = team_df[team_df["Team"].str.upper().str.contains(target, na=False)]
    if matches.empty:
        raise ValueError(f"Could not find team '{team_abbrev}' in FanGraphs data -- check abbreviation mapping.")
    return matches.iloc[0]


def build_team_offense(team_abbrev: str, opp_hand: str, season_batting, trailing_batting,
                        season_vs_hand: dict, trailing_vs_hand: dict,
                        xwoba_season: float = None, xwoba_l30: float = None) -> TeamOffense:
    season_row = find_team_row(season_batting, team_abbrev)
    trailing_row = find_team_row(trailing_batting, team_abbrev)

    def _get(row, key, default=None):
        try:
            return float(row[key])
        except (KeyError, TypeError, ValueError):
            return default

    return TeamOffense(
        wrc_plus_season=float(season_row["wRC+"]),
        wrc_plus_l30=float(trailing_row["wRC+"]),
        vs_opp_hand=HandednessSplit(
            wrc_plus_season=season_vs_hand.get(team_abbrev, {}).get(opp_hand, season_row["wRC+"]),
            wrc_plus_l30=trailing_vs_hand.get(team_abbrev, {}).get(opp_hand, trailing_row["wRC+"]),
        ),
        k_pct_season=_get(season_row, "K%"),
        k_pct_l30=_get(trailing_row, "K%"),
        contact_pct_season=_get(season_row, "Contact%"),
        contact_pct_l30=_get(trailing_row, "Contact%"),
        xwoba_season=xwoba_season,
        xwoba_l30=xwoba_l30,
    )


def build_starter(pitcher_name: str, throws: str, is_home: bool,
                   season_pitching_row, trailing_pitching_row,
                   home_era: float = None, road_era: float = None,
                   recent_ip_trend_short: bool = False,
                   xwoba_against_season: float = None, xwoba_against_l30: float = None) -> StarterPitcher:
    def _get(row, key, default=None):
        try:
            return float(row[key])
        except (KeyError, TypeError, ValueError):
            return default

    def _kbb(row):
        # FanGraphs pitching tables usually have K% and BB% as separate
        # columns rather than a combined K-BB%; compute it if both present.
        k = _get(row, "K%")
        bb = _get(row, "BB%")
        if k is not None and bb is not None:
            return k - bb
        return _get(row, "K-BB%")  # some pybaseball versions include this directly

    return StarterPitcher(
        name=pitcher_name,
        throws=throws,
        xfip_season=float(season_pitching_row["xFIP"]),
        xfip_l30=float(trailing_pitching_row["xFIP"]),
        home_era=home_era,
        road_era=road_era,
        is_home=is_home,
        recent_ip_trend_short=recent_ip_trend_short,
        k9_season=_get(season_pitching_row, "K/9"),
        k9_l30=_get(trailing_pitching_row, "K/9"),
        k_bb_pct_season=_kbb(season_pitching_row),
        k_bb_pct_l30=_kbb(trailing_pitching_row),
        xwoba_against_season=xwoba_against_season,
        xwoba_against_l30=xwoba_against_l30,
    )


def build_bullpen(team_abbrev: str, season_pitching, trailing_pitching,
                   xwoba_against_season: float = None, xwoba_against_l30: float = None) -> Bullpen:
    # NOTE: FanGraphs team_pitching includes starters + relievers combined.
    # For a cleaner bullpen-only number, filter pybaseball's reliever-specific
    # tables (pyb.team_pitching has a `ind`/role split in some versions) or use
    # bullpen-specific leaderboards. Using team-wide pitching as a placeholder
    # here -- flagged as a refinement to make once the pipeline is running.
    season_row = find_team_row(season_pitching, team_abbrev)
    trailing_row = find_team_row(trailing_pitching, team_abbrev)
    return Bullpen(
        xfip_season=float(season_row["xFIP"]),
        xfip_l30=float(trailing_row["xFIP"]),
        xwoba_against_season=xwoba_against_season,
        xwoba_against_l30=xwoba_against_l30,
    )


def build_park_weather(home_abbrev: str, game_date: str, game_hour_utc: int) -> ParkWeather:
    park = PARKS[home_abbrev]
    if park["roof"] == "fixed":
        return ParkWeather(park_factor=park["park_factor"], weather_run_pct=0.0)
    weather = df.get_weather(park["lat"], park["lon"], game_date, game_hour_utc)
    pct = df.weather_run_adjustment(weather, park["roof"])
    return ParkWeather(park_factor=park["park_factor"], weather_run_pct=pct)


def build_market(odds_event: dict, home_abbrev: str, away_abbrev: str) -> MarketLines:
    """odds_event is one event dict from The Odds API's h2h/spreads/totals response.
    Structure: event['bookmakers'][i]['markets'][j]['outcomes'] -- this pulls
    from the first available bookmaker; swap to a consensus/median across
    books for a sturdier line if you want less single-book noise."""
    book = odds_event["bookmakers"][0]
    markets = {m["key"]: m for m in book["markets"]}

    h2h = markets["h2h"]["outcomes"]
    home_ml = next(o["price"] for o in h2h if o["name"] == odds_event["home_team"])
    away_ml = next(o["price"] for o in h2h if o["name"] == odds_event["away_team"])

    totals = markets["totals"]["outcomes"]
    total_point = totals[0]["point"]

    spreads = markets["spreads"]["outcomes"]
    home_spread_outcome = next(o for o in spreads if o["name"] == odds_event["home_team"])

    return MarketLines(
        home_ml=home_ml,
        away_ml=away_ml,
        total=total_point,
        home_spread=home_spread_outcome["point"],
        home_spread_price=home_spread_outcome["price"],
    )


def build_sharp_splits(raw_splits: dict = None) -> SharpMoneySplits | None:
    if not raw_splits:
        return None
    return SharpMoneySplits(**raw_splits)
