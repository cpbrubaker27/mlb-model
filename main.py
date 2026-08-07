"""
Daily entry point. Run this once per day (via GitHub Actions cron):

    python main.py

Produces docs/index.html -- committed by the workflow and served via
GitHub Pages. Each game is processed independently and wrapped in
try/except so one bad data pull (missing pitcher stats, odds API hiccup,
etc.) doesn't take down the whole slate -- it just gets skipped with a
printed warning, and you still get every other game.
"""

import datetime as dt
import sys

import data_fetch as df
import build_game_inputs as bgi
from model import run_game
from render import render_game_card, render_page, render_overview_row, render_k_prop_watch
from parks import PARKS

SEASON = dt.date.today().year


def main():
    today = dt.date.today().isoformat()
    print(f"Building slate for {today}...")

    try:
        schedule = df.get_schedule(today)
    except Exception as e:
        print(f"FATAL: could not fetch schedule: {e}")
        sys.exit(1)

    if not schedule:
        print("No games today.")
        _write_page(today, [])
        return

    try:
        odds_events = df.get_odds()
    except Exception as e:
        print(f"WARNING: could not fetch odds ({e}) -- games will be skipped without market lines.")
        odds_events = []

    trailing_start, trailing_end = bgi.trailing_30_window(dt.date.today())

    try:
        season_scoring = df._fetch_with_retry(df.get_team_scoring_rates, SEASON)
        trailing_scoring = df._fetch_with_retry(df.get_team_scoring_rates, SEASON, trailing_start, trailing_end)
    except Exception as e:
        print(f"FATAL: could not fetch MLB Stats API team scoring rates after retries: {e}")
        sys.exit(1)

    # xwOBA is best-effort -- on failure these are just empty dicts, and the
    # model falls back to the actual-scoring-rate model as primary per game.
    # Computed once here (not per-game) since each call scrapes a full
    # season's worth of league-wide expected-stats data plus every team's
    # roster -- doing that once for the whole slate instead of once per
    # team per game avoids a lot of wasted, slow repeat fetching.
    season_xwoba = df.get_team_xwoba(SEASON)
    trailing_xwoba = {}  # no trailing-window version available -- see get_team_xwoba's note

    todays_starter_ids = {
        pid for game in schedule
        for pid in (game.get("home_probable_id"), game.get("away_probable_id"))
        if pid
    }
    team_bullpen_xwoba = df.get_all_bullpen_xwoba(SEASON, exclude_pitcher_ids=todays_starter_ids)

    cards = []
    overview_rows = []
    k_prop_rows = []
    for game in schedule:
        try:
            result = process_game(
                game, odds_events, season_scoring, trailing_scoring, today,
                season_xwoba, trailing_xwoba, team_bullpen_xwoba,
            )
            if result:
                overview_rows.append(result["overview_row"])
                cards.append(result["card"])
                k_prop_rows.extend(result["k_prop_rows"])
        except Exception as e:
            print(f"WARNING: skipping {game.get('away_team_abbrev')} @ {game.get('home_team_abbrev')}: {e}")

    k_prop_html = render_k_prop_watch(k_prop_rows)
    _write_page(today, overview_rows, cards, k_prop_html)
    print(f"Done. {len(cards)}/{len(schedule)} games rendered.")


def process_game(game, odds_events, season_scoring, trailing_scoring, today,
                  season_xwoba=None, trailing_xwoba=None, team_bullpen_xwoba=None):
    season_xwoba = season_xwoba or {}
    trailing_xwoba = trailing_xwoba or {}
    team_bullpen_xwoba = team_bullpen_xwoba or {}
    home_abbrev = game["home_team_abbrev"]
    away_abbrev = game["away_team_abbrev"]

    odds_event = _match_odds_event(odds_events, home_abbrev, away_abbrev)
    if odds_event is None:
        print(f"  no odds found for {away_abbrev} @ {home_abbrev}, skipping")
        return None

    market = bgi.build_market(odds_event, home_abbrev, away_abbrev)

    game_dt = dt.datetime.fromisoformat(game["game_time_utc"].replace("Z", "+00:00"))
    park_weather = bgi.build_park_weather(home_abbrev, today, game_dt.hour)

    home_pitcher_hand = "R"  # TODO: pull actual throwing hand from MLB Stats API /people/{id} endpoint
    away_pitcher_hand = "R"

    season = int(today[:4])

    home_offense = bgi.build_team_offense(home_abbrev, season_scoring, trailing_scoring,
                                           xwoba_season=season_xwoba.get(home_abbrev),
                                           xwoba_l30=trailing_xwoba.get(home_abbrev))
    away_offense = bgi.build_team_offense(away_abbrev, season_scoring, trailing_scoring,
                                           xwoba_season=season_xwoba.get(away_abbrev),
                                           xwoba_l30=trailing_xwoba.get(away_abbrev))

    home_starter_xwoba = df.get_pitcher_xwoba_against(game.get("home_probable_id"), game["home_probable"], season) if game["home_probable"] else None
    away_starter_xwoba = df.get_pitcher_xwoba_against(game.get("away_probable_id"), game["away_probable"], season) if game["away_probable"] else None

    home_era_splits = df.get_pitcher_home_road_era(game.get("home_probable_id"), season)
    away_era_splits = df.get_pitcher_home_road_era(game.get("away_probable_id"), season)

    home_pitcher_counts_season = df.get_pitcher_rate_stats(game.get("home_probable_id"), season)
    home_pitcher_counts_trailing = df.get_pitcher_rate_stats(
        game.get("home_probable_id"), season,
        *bgi.trailing_30_window(dt.date.fromisoformat(today)),
    )
    away_pitcher_counts_season = df.get_pitcher_rate_stats(game.get("away_probable_id"), season)
    away_pitcher_counts_trailing = df.get_pitcher_rate_stats(
        game.get("away_probable_id"), season,
        *bgi.trailing_30_window(dt.date.fromisoformat(today)),
    )
    home_pitcher_counts = {
        "k9_season": home_pitcher_counts_season.get("k9"),
        "k9_l30": home_pitcher_counts_trailing.get("k9"),
        "k_bb_pct_season": home_pitcher_counts_season.get("k_bb_pct"),
        "k_bb_pct_l30": home_pitcher_counts_trailing.get("k_bb_pct"),
    }
    away_pitcher_counts = {
        "k9_season": away_pitcher_counts_season.get("k9"),
        "k9_l30": away_pitcher_counts_trailing.get("k9"),
        "k_bb_pct_season": away_pitcher_counts_season.get("k_bb_pct"),
        "k_bb_pct_l30": away_pitcher_counts_trailing.get("k_bb_pct"),
    }

    home_starter = bgi.build_starter(game["home_probable"], home_pitcher_hand, True,
                                      home_pitcher_counts, home_era=home_era_splits.get("home_era"),
                                      xwoba_against_season=home_starter_xwoba, xwoba_against_l30=home_starter_xwoba)
    away_starter = bgi.build_starter(game["away_probable"], away_pitcher_hand, False,
                                      away_pitcher_counts, road_era=away_era_splits.get("road_era"),
                                      xwoba_against_season=away_starter_xwoba, xwoba_against_l30=away_starter_xwoba)

    home_bullpen = bgi.build_bullpen(xwoba_against_season=team_bullpen_xwoba.get(home_abbrev))
    away_bullpen = bgi.build_bullpen(xwoba_against_season=team_bullpen_xwoba.get(away_abbrev))

    sharp_raw = df.get_bet_splits(home_abbrev, away_abbrev)
    sharp = bgi.build_sharp_splits(sharp_raw)

    from model import TeamInputs, GameInputs
    home_inputs = TeamInputs(name=home_abbrev, offense=home_offense, starter=home_starter, bullpen=home_bullpen)
    away_inputs = TeamInputs(name=away_abbrev, offense=away_offense, starter=away_starter, bullpen=away_bullpen)

    game_inputs = GameInputs(home=home_inputs, away=away_inputs, park_weather=park_weather,
                              market=market, sharp=sharp)
    prediction = run_game(game_inputs)

    meta = {
        "home_abbrev": home_abbrev,
        "away_abbrev": away_abbrev,
        "home_team_id": game.get("home_team_id"),
        "away_team_id": game.get("away_team_id"),
        "time_str": game_dt.strftime("%-I:%M %p UTC"),
    }
    card = render_game_card(meta, home_inputs, away_inputs, park_weather, market, prediction)
    overview_row = render_overview_row(meta, market, prediction)

    k_prop_rows = []
    if prediction.away_sp_k_proj is not None and away_starter.k9_l30 is not None:
        blended_k9 = 0.5 * (away_starter.k9_l30 or away_starter.k9_season) + 0.5 * (away_starter.k9_season or away_starter.k9_l30)
        k_prop_rows.append({"pitcher": away_starter.name, "team": away_abbrev, "opp": home_abbrev,
                             "k9": blended_k9, "k_proj": prediction.away_sp_k_proj})
    if prediction.home_sp_k_proj is not None and home_starter.k9_l30 is not None:
        blended_k9 = 0.5 * (home_starter.k9_l30 or home_starter.k9_season) + 0.5 * (home_starter.k9_season or home_starter.k9_l30)
        k_prop_rows.append({"pitcher": home_starter.name, "team": home_abbrev, "opp": away_abbrev,
                             "k9": blended_k9, "k_proj": prediction.home_sp_k_proj})

    return {"card": card, "overview_row": overview_row, "k_prop_rows": k_prop_rows}


def _match_odds_event(odds_events, home_abbrev, away_abbrev):
    home_name = PARKS.get(home_abbrev, {}).get("team", "")
    away_name = PARKS.get(away_abbrev, {}).get("team", "")
    for event in odds_events:
        if home_name and home_name in event.get("home_team", "") and \
           away_name and away_name in event.get("away_team", ""):
            return event
    return None


def _write_page(date_str, overview_rows, cards, k_prop_html=""):
    html = render_page(date_str, overview_rows, cards, k_prop_html)
    with open("docs/index.html", "w") as f:
        f.write(html)


if __name__ == "__main__":
    main()
