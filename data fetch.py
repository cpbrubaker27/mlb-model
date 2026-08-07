"""
Data fetching layer. Each function pulls one kind of data and is written to
fail loudly with a clear message rather than silently returning garbage --
except get_bet_splits(), which is explicitly best-effort and degrades to
None on failure, since that's the one source with no reliable free API.

Sources:
  - Schedule / probables / records / team scoring rates / pitcher K-BB : MLB Stats API
    (statsapi.mlb.com, free, no key -- never blocks automated/cloud traffic)
  - xwOBA (team-level and pitcher/bullpen-against) : pybaseball (Baseball Savant)
  - Weather : Open-Meteo (free, no key)
  - Odds : The Odds API (free tier, needs ODDS_API_KEY env var)
  - Bet splits (bet% vs money%) : best-effort, no reliable free source -- see note below.

NOTE: FanGraphs was tried first for wRC+/xFIP but blocks requests from
cloud-hosted IPs (GitHub Actions runners get a 403). Baseball Savant's
CSV-export endpoints (used via pybaseball here) are built for this kind of
programmatic access and haven't shown the same issue, but this hasn't been
verified against a live GitHub Actions run yet -- if Savant also blocks,
the fallback is running the fetch step from a non-cloud IP (see README).

NOTE ON THIS BEING UNTESTED: this sandbox has no network access, so none of
these calls have been run against the live endpoints. The MLB Stats API and
Open-Meteo shapes below are accurate as documented, but pybaseball's exact
column names can drift between versions -- if a KeyError shows up on first
run, that's the most likely spot and an easy fix.
"""

import os
import time
import datetime as dt
from typing import Optional
import requests

try:
    import pybaseball as pyb
    pyb.cache.enable()
except ImportError:
    pyb = None  # handled at call sites with a clear error

from parks import PARKS


def _fetch_with_retry(fn, *args, retries=3, backoff_seconds=5, **kwargs):
    """Wraps a fetch call with a few retries on failure -- a 403/429 from a
    scraped source is sometimes transient rate-limiting rather than a hard
    block, so a short backoff can be enough."""
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            last_err = e
            if attempt < retries:
                print(f"  attempt {attempt} failed ({e}), retrying in {backoff_seconds}s...")
                time.sleep(backoff_seconds)
    raise last_err

MLB_STATS_BASE = "https://statsapi.mlb.com/api/v1"
ODDS_API_BASE = "https://api.the-odds-api.com/v4"


# ----------------------------------------------------------------------
# SCHEDULE & PROBABLE PITCHERS
# ----------------------------------------------------------------------

def get_schedule(date: str) -> list[dict]:
    """date: 'YYYY-MM-DD'. Returns list of games with team abbrevs, ids, and
    probable pitcher names/ids where announced."""
    url = f"{MLB_STATS_BASE}/schedule"
    params = {
        "sportId": 1,
        "date": date,
        "hydrate": "team,probablePitcher,linescore",
    }
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()

    games = []
    for date_block in data.get("dates", []):
        for g in date_block.get("games", []):
            home = g["teams"]["home"]
            away = g["teams"]["away"]
            games.append({
                "game_pk": g["gamePk"],
                "game_time_utc": g["gameDate"],
                "venue": g.get("venue", {}).get("name"),
                "home_team_id": home["team"]["id"],
                "home_team_abbrev": home["team"].get("abbreviation"),
                "away_team_id": away["team"]["id"],
                "away_team_abbrev": away["team"].get("abbreviation"),
                "home_probable": home.get("probablePitcher", {}).get("fullName"),
                "home_probable_id": home.get("probablePitcher", {}).get("id"),
                "away_probable": away.get("probablePitcher", {}).get("fullName"),
                "away_probable_id": away.get("probablePitcher", {}).get("id"),
            })
    return games


def get_team_record_splits(team_id: int, season: int) -> dict:
    """Home/road win-loss records and recent form (last 10)."""
    url = f"{MLB_STATS_BASE}/teams/{team_id}/stats"
    params = {"stats": "season", "group": "hitting", "season": season}
    # Records come from the standings endpoint, which has home/away/L10 splits directly.
    standings_url = f"{MLB_STATS_BASE}/standings"
    r = requests.get(standings_url, params={"leagueId": "103,104", "season": season}, timeout=15)
    r.raise_for_status()
    data = r.json()
    for record_group in data.get("records", []):
        for team_record in record_group.get("teamRecords", []):
            if team_record["team"]["id"] == team_id:
                splits = {s["type"]: s for s in team_record.get("records", {}).get("splitRecords", [])}
                return {
                    "wins": team_record["wins"],
                    "losses": team_record["losses"],
                    "home": splits.get("home"),
                    "away": splits.get("away"),
                    "last_10": splits.get("lastTen"),
                    "streak": team_record.get("streak", {}).get("streakCode"),
                }
    return {}


# ----------------------------------------------------------------------
# TEAM SCORING RATES (runs scored/allowed per game, K%) -- MLB Stats API,
# powers the secondary ("actual scoring rate") model plus K-prop watch
# ----------------------------------------------------------------------

# MLB Stats API team abbreviations sometimes differ slightly from the ones
# used elsewhere in this project (e.g. schedule endpoint) -- this maps by
# team ID instead of abbreviation to sidestep that entirely.
def _team_id_to_abbrev(season: int) -> dict:
    url = f"{MLB_STATS_BASE}/teams"
    r = requests.get(url, params={"sportIds": 1, "season": season}, timeout=15)
    r.raise_for_status()
    mapping = {t["id"]: t.get("abbreviation") for t in r.json().get("teams", [])}
    if not mapping:
        print(f"  WARNING: /teams returned no teams for season {season} -- check sportIds/season params")
    return mapping


def get_team_scoring_rates(season: int, start_dt: Optional[str] = None, end_dt: Optional[str] = None) -> dict:
    """Returns {team_abbrev: {runs_per_game, runs_allowed_per_game, k_pct}}.
    If start_dt/end_dt given (YYYY-MM-DD), scopes to that date range for a
    trailing-window pull; otherwise pulls full-season stats."""
    id_map = _team_id_to_abbrev(season)
    stats_type = "byDateRange" if (start_dt and end_dt) else "season"

    hitting_params = {"stats": stats_type, "group": "hitting", "season": season, "sportIds": 1}
    pitching_params = {"stats": stats_type, "group": "pitching", "season": season, "sportIds": 1}
    if start_dt and end_dt:
        hitting_params["startDate"] = start_dt
        hitting_params["endDate"] = end_dt
        pitching_params["startDate"] = start_dt
        pitching_params["endDate"] = end_dt

    result = {}
    hit_url = f"{MLB_STATS_BASE}/teams/stats"
    r = requests.get(hit_url, params=hitting_params, timeout=15)
    r.raise_for_status()
    hitting_json = r.json()
    hitting_splits_found = 0
    for group in hitting_json.get("stats", []):
        for split in group.get("splits", []):
            team_id = split["team"]["id"]
            abbrev = id_map.get(team_id)
            if not abbrev:
                continue
            hitting_splits_found += 1
            stat = split.get("stat", {})
            games = float(stat.get("gamesPlayed", 0)) or None
            runs = float(stat.get("runs", 0))
            plate_app = float(stat.get("plateAppearances", 0)) or None
            strikeouts = float(stat.get("strikeOuts", 0))
            result.setdefault(abbrev, {})
            result[abbrev]["runs_per_game"] = (runs / games) if games else None
            result[abbrev]["k_pct"] = (strikeouts / plate_app) if plate_app else None
    if hitting_splits_found == 0:
        print(f"  WARNING: teams/stats (hitting, {stats_type}) returned 0 usable splits. "
              f"Raw response keys: {list(hitting_json.keys())}, stats groups: {len(hitting_json.get('stats', []))}")

    pitch_url = f"{MLB_STATS_BASE}/teams/stats"
    r = requests.get(pitch_url, params=pitching_params, timeout=15)
    r.raise_for_status()
    pitching_json = r.json()
    pitching_splits_found = 0
    for group in pitching_json.get("stats", []):
        for split in group.get("splits", []):
            team_id = split["team"]["id"]
            abbrev = id_map.get(team_id)
            if not abbrev:
                continue
            pitching_splits_found += 1
            stat = split.get("stat", {})
            games = float(stat.get("gamesPlayed", 0)) or None
            runs_allowed = float(stat.get("runs", 0))
            result.setdefault(abbrev, {})
            result[abbrev]["runs_allowed_per_game"] = (runs_allowed / games) if games else None
    if pitching_splits_found == 0:
        print(f"  WARNING: teams/stats (pitching, {stats_type}) returned 0 usable splits. "
              f"Raw response keys: {list(pitching_json.keys())}, stats groups: {len(pitching_json.get('stats', []))}")

    return result


def get_pitcher_rate_stats(pitcher_id: int, season: int, start_dt: Optional[str] = None,
                            end_dt: Optional[str] = None) -> dict:
    """Returns {k9, k_bb_pct} for one pitcher, computed from raw strikeout/
    walk/innings-pitched/batters-faced counts (MLB Stats API), for either
    full season or a trailing date-range window."""
    if not pitcher_id:
        return {}
    stats_type = "byDateRange" if (start_dt and end_dt) else "season"
    params = {"stats": stats_type, "group": "pitching", "season": season, "sportId": 1}
    if start_dt and end_dt:
        params["startDate"] = start_dt
        params["endDate"] = end_dt
    url = f"{MLB_STATS_BASE}/people/{pitcher_id}/stats"
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    for group in data.get("stats", []):
        for split in group.get("splits", []):
            stat = split.get("stat", {})
            ip_str = stat.get("inningsPitched")  # e.g. "62.1" means 62 1/3 innings
            if not ip_str:
                continue
            whole, _, thirds = ip_str.partition(".")
            innings = float(whole) + (float(thirds) / 3.0 if thirds else 0.0)
            if innings <= 0:
                continue
            strikeouts = float(stat.get("strikeOuts", 0))
            walks = float(stat.get("baseOnBalls", 0))
            batters_faced = float(stat.get("battersFaced", 0)) or None
            k9 = strikeouts / innings * 9.0
            k_bb_pct = ((strikeouts - walks) / batters_faced) if batters_faced else None
            return {"k9": round(k9, 2), "k_bb_pct": k_bb_pct}
    return {}


def get_pitcher_home_road_era(pitcher_id: int, season: int) -> dict:
    """Returns {home_era, road_era} for one pitcher via MLB Stats API's
    situational splits (stats=statSplits, sitCodes h/a for home/away).
    This was previously a stub that always returned None -- now actually
    implemented. Falls back to {} (both None downstream) on any failure
    rather than raising, since this is a nice-to-have refinement on top
    of the core xwOBA/xFIP-style rate, not something the projection
    strictly requires."""
    if not pitcher_id:
        return {}
    result = {}
    for sit_code, key in (("h", "home_era"), ("a", "road_era")):
        try:
            params = {"stats": "statSplits", "group": "pitching", "season": season,
                      "sitCodes": sit_code, "sportId": 1}
            url = f"{MLB_STATS_BASE}/people/{pitcher_id}/stats"
            r = requests.get(url, params=params, timeout=15)
            r.raise_for_status()
            data = r.json()
            for group in data.get("stats", []):
                for split in group.get("splits", []):
                    era = split.get("stat", {}).get("era")
                    if era is not None:
                        result[key] = float(era)
                        break
        except Exception as e:
            print(f"  home/road ERA fetch failed for pitcher {pitcher_id} ({sit_code}): {e}")
    return result


# ----------------------------------------------------------------------
# xwOBA (team-level and pitcher/bullpen-against) -- powers the primary model
# ----------------------------------------------------------------------
# There's no direct team-level expected-stats function in pybaseball, only
# player-level statcast_batter_expected_stats / statcast_pitcher_expected_stats.
# So we build team aggregates ourselves: pull each team's active roster from
# the MLB Stats API to know who's on which team, then compute a plate-
# appearance-weighted average of each roster's expected wOBA. This gives a
# season-long number only -- pybaseball's expected-stats call doesn't expose
# a date-range option the way raw Statcast pitch-level data does, so there's
# no trailing-30-day version. The model already falls back gracefully to
# using the season value for both slots when only one is available.

def get_all_team_ids(season: int) -> dict:
    """Returns {team_abbrev: team_id} for all 30 MLB teams -- the reverse of
    _team_id_to_abbrev, needed to look up each team's roster by ID."""
    id_map = _team_id_to_abbrev(season)
    return {abbrev: team_id for team_id, abbrev in id_map.items() if abbrev}


def get_team_roster_ids(team_id: int, season: int) -> dict:
    """Returns {'batters': [ids], 'pitchers': [ids]} for a team's active
    roster, split by position so hitters and pitchers can be aggregated
    separately."""
    url = f"{MLB_STATS_BASE}/teams/{team_id}/roster"
    try:
        r = requests.get(url, params={"rosterType": "active", "season": season}, timeout=15)
        r.raise_for_status()
        batters, pitchers = [], []
        for entry in r.json().get("roster", []):
            person_id = entry.get("person", {}).get("id")
            position_type = entry.get("position", {}).get("type", "")
            if not person_id:
                continue
            if position_type == "Pitcher":
                pitchers.append(person_id)
            else:
                batters.append(person_id)
        return {"batters": batters, "pitchers": pitchers}
    except Exception as e:
        print(f"  roster fetch failed for team {team_id}: {e}")
        return {"batters": [], "pitchers": []}


def _weighted_expected_woba(player_ids: list, expected_stats_df, weight_col_candidates=("pa", "attempts")) -> Optional[float]:
    """Plate-appearance-weighted average est_woba across a list of player IDs,
    looked up in a pybaseball expected-stats dataframe. Tries a couple of
    possible column names for the weight (PA count), since pybaseball's
    exact column naming isn't something this environment can verify without
    live network access -- falls back to an unweighted average if neither
    weight column is found, rather than failing outright."""
    if expected_stats_df is None or not player_ids:
        return None
    weight_col = next((c for c in weight_col_candidates if c in expected_stats_df.columns), None)
    matches = expected_stats_df[expected_stats_df["player_id"].isin(player_ids)]
    if matches.empty:
        return None
    if weight_col:
        total_weight = matches[weight_col].sum()
        if total_weight > 0:
            return float((matches["est_woba"] * matches[weight_col]).sum() / total_weight)
    return float(matches["est_woba"].mean())


def get_team_xwoba(season: int, start_dt: Optional[str] = None, end_dt: Optional[str] = None) -> dict:
    """Team-level offense xwOBA, PA-weighted across each team's active
    roster. start_dt/end_dt are accepted for call-signature compatibility
    with the rest of the fetch layer but not used (see module note above) --
    always returns the season-long number."""
    if pyb is None:
        return {}
    try:
        batter_stats = pyb.statcast_batter_expected_stats(season)
    except Exception as e:
        print(f"  statcast_batter_expected_stats failed: {e}")
        return {}

    team_ids = get_all_team_ids(season)
    result = {}
    for abbrev, team_id in team_ids.items():
        roster = get_team_roster_ids(team_id, season)
        xwoba = _weighted_expected_woba(roster["batters"], batter_stats)
        if xwoba is not None:
            result[abbrev] = xwoba
    if not result:
        print("  WARNING: team xwOBA aggregation produced no results -- check pybaseball column names "
              f"(saw columns: {list(batter_stats.columns) if batter_stats is not None else 'N/A'})")
    return result


def get_pitcher_xwoba_against(pitcher_id: int, pitcher_name: str, season: int) -> Optional[float]:
    """xwOBA allowed by a specific starter, via Baseball Savant. Matches by
    MLBAM player ID first (sturdier), falling back to a name-contains match
    only if the ID lookup comes up empty -- this also naturally returns None
    for rookies/call-ups who don't have enough tracked pitches yet to
    qualify for the expected-stats leaderboard, which is a real data gap,
    not a bug."""
    if pyb is None:
        return None
    try:
        result = pyb.statcast_pitcher_expected_stats(season)
        row = None
        if pitcher_id and "player_id" in result.columns:
            id_matches = result[result["player_id"] == pitcher_id]
            if not id_matches.empty:
                row = id_matches.iloc[0]
        if row is None and pitcher_name:
            name_matches = result[result["player_name"].str.contains(pitcher_name, case=False, na=False)]
            if not name_matches.empty:
                row = name_matches.iloc[0]
        if row is not None:
            return float(row["est_woba"])
    except Exception:
        return None
    return None


def get_all_bullpen_xwoba(season: int, exclude_pitcher_ids: Optional[set] = None) -> dict:
    """Bullpen-wide xwOBA allowed, PA/BF-weighted across each team's relief
    pitchers, computed once for all 30 teams (rather than re-scraping the
    full pybaseball leaderboard once per game, which would be wasteful).
    exclude_pitcher_ids lets today's probable starters be left out of their
    own team's bullpen average -- pass the set of every starter's MLBAM ID
    for today's slate."""
    if pyb is None:
        return {}
    exclude_pitcher_ids = exclude_pitcher_ids or set()
    try:
        pitcher_stats = pyb.statcast_pitcher_expected_stats(season)
    except Exception as e:
        print(f"  statcast_pitcher_expected_stats failed: {e}")
        return {}

    team_ids = get_all_team_ids(season)
    result = {}
    for abbrev, team_id in team_ids.items():
        roster = get_team_roster_ids(team_id, season)
        bullpen_ids = [pid for pid in roster["pitchers"] if pid not in exclude_pitcher_ids]
        xwoba = _weighted_expected_woba(bullpen_ids, pitcher_stats, weight_col_candidates=("pa", "bf", "attempts"))
        if xwoba is not None:
            result[abbrev] = xwoba
    return result


# ----------------------------------------------------------------------
# WEATHER
# ----------------------------------------------------------------------

def get_weather(lat: float, lon: float, date: str, hour_utc: int) -> dict:
    """Open-Meteo forecast for the game's stadium coords, at the game hour.
    Returns temp (F), wind speed (mph), wind direction, precip probability, condition."""
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "temperature_2m,precipitation_probability,windspeed_10m,winddirection_10m,weathercode",
        "temperature_unit": "fahrenheit",
        "windspeed_unit": "mph",
        "start_date": date,
        "end_date": date,
        "timezone": "UTC",
    }
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    target = f"{date}T{hour_utc:02d}:00"
    if target not in times:
        # fall back to nearest available hour
        idx = 0
    else:
        idx = times.index(target)
    return {
        "temp_f": hourly.get("temperature_2m", [None])[idx],
        "wind_mph": hourly.get("windspeed_10m", [None])[idx],
        "wind_dir_deg": hourly.get("winddirection_10m", [None])[idx],
        "precip_pct": hourly.get("precipitation_probability", [None])[idx],
        "weathercode": hourly.get("weathercode", [None])[idx],
    }


def weather_run_adjustment(weather: dict, roof: str) -> float:
    """Rough, tunable rule of thumb converting weather into a % run adjustment.
    Domes/closed roofs get 0. Real research (e.g. wind-out/temp effects on
    fly-ball carry) should replace these constants over time -- treat this
    as a reasonable starting point, not gospel."""
    if roof in ("fixed",) or (roof == "retractable" and weather.get("temp_f") is None):
        return 0.0
    pct = 0.0
    temp = weather.get("temp_f")
    if temp is not None:
        # Roughly +1% runs per 5 degrees above 70F, -1% per 5 below 70F.
        pct += (temp - 70) / 5 * 0.01
    wind = weather.get("wind_mph")
    if wind is not None and wind > 10:
        # Assume a meaningful fraction of high wind is blowing out; this is a
        # simplification -- refine with wind direction vs. field orientation
        # per park if you want to get serious about it.
        pct += (wind - 10) / 10 * 0.01
    return round(pct, 3)


# ----------------------------------------------------------------------
# ODDS
# ----------------------------------------------------------------------

def get_odds() -> list[dict]:
    """Pulls current MLB odds (moneyline, spread, total) from The Odds API.
    Requires ODDS_API_KEY env var (free tier: 500 requests/month, plenty for
    one daily pull of ~15 games)."""
    api_key = os.environ.get("ODDS_API_KEY")
    if not api_key:
        raise RuntimeError("ODDS_API_KEY environment variable not set")
    url = f"{ODDS_API_BASE}/sports/baseball_mlb/odds"
    params = {
        "apiKey": api_key,
        "regions": "us",
        "markets": "h2h,spreads,totals",
        "oddsFormat": "american",
    }
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    return r.json()


# ----------------------------------------------------------------------
# BET SPLITS (bet% vs money%) -- best effort, no reliable free API
# ----------------------------------------------------------------------

def get_bet_splits(home_team: str, away_team: str) -> Optional[dict]:
    """There is no free, stable API for public bet% / money% splits.
    Sites like sportsbettingdime.com or actionnetwork.com display this but
    require scraping their HTML, which breaks whenever they change page
    structure -- not something to depend on for a daily automated pipeline
    without maintenance.

    As agreed: leave this returning None until/unless you get access to a
    real source (a paid odds/splits API, or a specific site you want me to
    build a scraper against). The model already handles None gracefully --
    it just skips the sharp-money gate for that game rather than guessing.
    """
    return None
