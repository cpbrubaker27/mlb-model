"""
Data fetching layer. Each function pulls one kind of data and is written to
fail loudly with a clear message rather than silently returning garbage --
except get_bet_splits(), which is explicitly best-effort and degrades to
None on failure, since that's the one source with no reliable free API.

Sources:
  - Schedule / probables / team records : MLB Stats API (statsapi.mlb.com, free, no key)
  - Advanced batting/pitching (wRC+, xFIP, ISO, K%, etc.) : pybaseball (FanGraphs scraper)
  - xERA (Statcast) : pybaseball (Baseball Savant)
  - Weather : Open-Meteo (free, no key)
  - Odds : The Odds API (free tier, needs ODDS_API_KEY env var)
  - Bet splits (bet% vs money%) : best-effort, no reliable free source -- see note below.

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

# ----------------------------------------------------------------------
# FanGraphs (and similar sites) often block requests coming from cloud/
# data-center IP ranges -- which is exactly what GitHub Actions runners
# use -- by checking for a browser-like User-Agent header. pybaseball's
# default requests don't set one, so we patch requests globally here to
# add one before any pybaseball calls happen. This is the standard first
# fix for a 403 on a scrape-based source; if FanGraphs is doing deeper
# bot detection (JS challenges, TLS fingerprinting) this patch alone
# won't be enough, and the correct fallback is running the fetch step
# from a non-data-center IP instead (e.g. a self-hosted runner on your
# own machine) rather than fighting the block further.
# ----------------------------------------------------------------------
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

_orig_requests_get = requests.get

def _patched_get(url, **kwargs):
    headers = kwargs.pop("headers", None) or {}
    merged = {**_BROWSER_HEADERS, **headers}
    return _orig_requests_get(url, headers=merged, **kwargs)

requests.get = _patched_get


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
# BATTING (wRC+, OPS, ISO, K%) -- season and trailing 30 days, via FanGraphs
# ----------------------------------------------------------------------

def get_team_batting(season: int, start_dt: Optional[str] = None, end_dt: Optional[str] = None):
    """Returns a pybaseball DataFrame of FanGraphs team batting stats.
    If start_dt/end_dt given (YYYY-MM-DD), scopes to that date range
    (use this for the trailing-30-days pull)."""
    if pyb is None:
        raise RuntimeError("pybaseball not installed -- pip install pybaseball")
    if start_dt and end_dt:
        return pyb.team_batting(season, start_dt=start_dt, end_dt=end_dt)
    return pyb.team_batting(season)


def get_team_batting_vs_hand(season: int, hand: str, start_dt: Optional[str] = None, end_dt: Optional[str] = None):
    """wRC+ vs LHP or RHP. hand: 'L' or 'R'.
    pybaseball doesn't expose a single clean vs-hand team split call, so this
    pulls from FanGraphs' split leaderboard endpoint via team_batting with the
    appropriate split filter. If your pybaseball version doesn't support this
    directly, the fallback is scraping FanGraphs' "Splits Leaderboards" page
    for wRC+ vs LHP/RHP by team -- flagged here as the most likely thing to
    need a manual adjustment on first run."""
    if pyb is None:
        raise RuntimeError("pybaseball not installed -- pip install pybaseball")
    # placeholder -- see docstring. pyb.team_batting supports a `split` kwarg
    # in some versions; verify against installed version on first run.
    raise NotImplementedError(
        "Wire this to your pybaseball version's vs-hand split support, "
        "or scrape FanGraphs splits leaderboard as a fallback."
    )


# ----------------------------------------------------------------------
# PITCHING (starters + bullpen: ERA, xFIP, K/9) -- season and trailing
# ----------------------------------------------------------------------

def get_team_pitching(season: int, start_dt: Optional[str] = None, end_dt: Optional[str] = None):
    if pyb is None:
        raise RuntimeError("pybaseball not installed -- pip install pybaseball")
    if start_dt and end_dt:
        return pyb.team_pitching(season, start_dt=start_dt, end_dt=end_dt)
    return pyb.team_pitching(season)


def get_pitcher_xera(pitcher_name: str, season: int) -> Optional[float]:
    """xERA (Statcast expected ERA) via Baseball Savant. pybaseball exposes
    this through statcast_pitcher / pitcher expected stats leaderboards --
    exact call depends on installed pybaseball version. Returns None if
    unavailable rather than raising, since xERA is a nice-to-have alongside
    xFIP, not strictly required by the model."""
    if pyb is None:
        return None
    try:
        # pyb.statcast_pitcher_expected_stats(season) is the typical entry
        # point in recent pybaseball versions -- verify column names on first run.
        df = pyb.statcast_pitcher_expected_stats(season)
        row = df[df["player_name"].str.contains(pitcher_name, case=False, na=False)]
        if not row.empty:
            return float(row.iloc[0]["est_era"])
    except Exception:
        return None
    return None


# ----------------------------------------------------------------------
# xwOBA (team-level and pitcher-against) -- powers the secondary model
# ----------------------------------------------------------------------

def get_team_xwoba(season: int, start_dt: Optional[str] = None, end_dt: Optional[str] = None) -> dict:
    """Team-level xwOBA via Baseball Savant, keyed by team abbreviation.
    pybaseball's statcast_team_expected_stats (or equivalent Savant team
    leaderboard call) is the likely entry point -- verify the exact function
    name/columns against your installed pybaseball version on first run,
    same caveat as get_pitcher_xera. Returns {} on failure so the model
    gracefully falls back to the recency-weighted secondary model instead
    of blocking the whole pipeline on one flaky source."""
    if pyb is None:
        return {}
    try:
        df = pyb.statcast_team_expected_stats(season, start_dt=start_dt, end_dt=end_dt) \
            if start_dt else pyb.statcast_team_expected_stats(season)
        return {row["team_abbrev"]: float(row["est_woba"]) for _, row in df.iterrows()}
    except Exception as e:
        print(f"  xwOBA fetch failed (falling back to non-xwOBA secondary model): {e}")
        return {}


def get_pitcher_xwoba_against(pitcher_name: str, season: int) -> Optional[float]:
    """xwOBA allowed by a specific starter, via Baseball Savant."""
    if pyb is None:
        return None
    try:
        df = pyb.statcast_pitcher_expected_stats(season)
        row = df[df["player_name"].str.contains(pitcher_name, case=False, na=False)]
        if not row.empty:
            return float(row.iloc[0]["est_woba"])
    except Exception:
        return None
    return None


def get_bullpen_xwoba_against(team_abbrev: str, season: int) -> Optional[float]:
    """Bullpen-wide xwOBA allowed. Same caveat as get_team_xwoba re: exact
    pybaseball call -- this likely needs filtering statcast_pitcher-level
    data down to relief appearances for the team, which pybaseball doesn't
    expose as a single clean call. Flagged as a refinement; returns None
    (graceful fallback) until wired up."""
    return None


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
