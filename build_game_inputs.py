"""
Turns raw data from data_fetch.py into the GameInputs objects model.py expects.
This is the "glue" layer -- if a stat source's field names drift, this is
where you'll patch it, not in model.py itself.

Sourcing note: this pulls from the MLB Stats API (team runs scored/allowed,
K/BB counts, records) and Baseball Savant via pybaseball (xwOBA, xwOBA-
against). It deliberately does NOT use FanGraphs -- FanGraphs blocks
requests from cloud-hosted IPs like GitHub Actions runners (a 403), while
both of these sources are built for this kind of programmatic access.
"""

import datetime as dt
from model import (
    TeamOffense, StarterPitcher, Bullpen,
    ParkWeather, MarketLines, TeamInputs, GameInputs, SharpMoneySplits,
)
from parks import PARKS
import data_fetch as df


def trailing_30_window(as_of: dt.date) -> tuple[str, str]:
    start = as_of - dt.timedelta(days=30)
    return start.isoformat(), as_of.isoformat()


def build_team_offense(team_abbrev: str, season_stats: dict, trailing_stats: dict,
                        xwoba_season: float = None, xwoba_l30: float = None) -> TeamOffense:
    """season_stats/trailing_stats are dicts keyed by team abbreviation, as
    returned by data_fetch.get_team_scoring_rates() -- each value has
    runs_per_game, runs_allowed_per_game, k_pct (all optional, None if
    that team's row wasn't found for some reason)."""
    s = season_stats.get(team_abbrev, {})
    t = trailing_stats.get(team_abbrev, {})
    return TeamOffense(
        xwoba_season=xwoba_season,
        xwoba_l30=xwoba_l30,
        runs_scored_pg_season=s.get("runs_per_game"),
        runs_scored_pg_l30=t.get("runs_per_game"),
        runs_allowed_pg_season=s.get("runs_allowed_per_game"),
        runs_allowed_pg_l30=t.get("runs_allowed_per_game"),
        k_pct_season=s.get("k_pct"),
        k_pct_l30=t.get("k_pct"),
        # contact_pct isn't available from the MLB Stats API or Savant's team
        # tables -- was previously sourced from FanGraphs. Left unpopulated;
        # project_strikeouts() already handles this gracefully (falls back
        # to K% alone). Could be added later via Savant's swing/take
        # leaderboard if it becomes worth the extra fetch.
    )


def build_starter(pitcher_name: str, throws: str, is_home: bool,
                   pitcher_counts: dict,
                   home_era: float = None, road_era: float = None,
                   recent_ip_trend_short: bool = False,
                   xwoba_against_season: float = None, xwoba_against_l30: float = None) -> StarterPitcher:
    """pitcher_counts: dict with keys k9_season, k9_l30, k_bb_pct_season,
    k_bb_pct_l30, as computed by data_fetch.get_pitcher_rate_stats() from
    raw MLB Stats API strikeout/walk/innings counts."""
    return StarterPitcher(
        name=pitcher_name,
        throws=throws,
        home_era=home_era,
        road_era=road_era,
        is_home=is_home,
        recent_ip_trend_short=recent_ip_trend_short,
        k9_season=pitcher_counts.get("k9_season"),
        k9_l30=pitcher_counts.get("k9_l30"),
        k_bb_pct_season=pitcher_counts.get("k_bb_pct_season"),
        k_bb_pct_l30=pitcher_counts.get("k_bb_pct_l30"),
        xwoba_against_season=xwoba_against_season,
        xwoba_against_l30=xwoba_against_l30,
    )


def build_bullpen(xwoba_against_season: float = None, xwoba_against_l30: float = None) -> Bullpen:
    return Bullpen(
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
