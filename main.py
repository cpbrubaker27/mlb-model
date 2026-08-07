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
        season_batting = df._fetch_with_retry(df.get_team_batting, SEASON)
        trailing_batting = df._fetch_with_retry(df.get_team_batting, SEASON, trailing_start, trailing_end)
        season_pitching = df._fetch_with_retry(df.get_team_pitching, SEASON)
        trailing_pitching = df._fetch_with_retry(df.get_team_pitching, SEASON, trailing_start, trailing_end)
    except Exception as e:
        print(f"FATAL: could not fetch FanGraphs season/trailing stats after retries: {e}")
        sys.exit(1)

    # xwOBA is best-effort -- on failure these are just empty dicts, and the
    # model falls back to its recency-weighted secondary model per game.
    season_xwoba = df.get_team_xwoba(SEASON)
    trailing_xwoba = df.get_team_xwoba(SEASON, trailing_start, trailing_end)

    cards = []
    overview_rows = []
    k_prop_rows = []
    for game in schedule:
        try:
            result = process_game(
                game, odds_events, season_batting, trailing_batting,
                season_pitching, trailing_pitching, today,
                season_xwoba, trailing_xwoba,
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


def process_game(game, odds_events, season_batting, trailing_batting,
                  season_pitching, trailing_pitching, today,
                  season_xwoba=None, trailing_xwoba=None):
    season_xwoba = season_xwoba or {}
    trailing_xwoba = trailing_xwoba or {}
    home_abbrev = game["home_team_abbrev"]
    away_abbrev = game["away_team_abbrev"]

    odds_event = _match_odds_event(odds_events, home_abbrev, away_abbrev)
    if odds_event is None:
        print(f"  no odds found for {away_abbrev} @ {home_abbrev}, skipping")
        return None

    market = bgi.build_market(odds_event, home_abbrev, away_abbrev)

    game_dt = dt.datetime.fromisoformat(game["game_time_utc"].replace("Z", "+00:00"))
    park_weather = bgi.build_park_weather(home_abbrev, today, game_dt.hour)

    # vs-hand splits: left as empty dicts until build_team_offense's fallback
    # (season/trailing overall wRC+) is replaced with real vs-hand data --
    # see NotImplementedError note in data_fetch.get_team_batting_vs_hand.
    season_vs_hand, trailing_vs_hand = {}, {}

    home_pitcher_hand = "R"  # TODO: pull actual throwing hand from MLB Stats API people endpoint
    away_pitcher_hand = "R"

    home_offense = bgi.build_team_offense(home_abbrev, away_pitcher_hand, season_batting,
                                           trailing_batting, season_vs_hand, trailing_vs_hand,
                                           xwoba_season=season_xwoba.get(home_abbrev),
                                           xwoba_l30=trailing_xwoba.get(home_abbrev))
    away_offense = bgi.build_team_offense(away_abbrev, home_pitcher_hand, season_batting,
                                           trailing_batting, season_vs_hand, trailing_vs_hand,
                                           xwoba_season=season_xwoba.get(away_abbrev),
                                           xwoba_l30=trailing_xwoba.get(away_abbrev))

    home_season_p = bgi.find_team_row(season_pitching, home_abbrev)
    home_trailing_p = bgi.find_team_row(trailing_pitching, home_abbrev)
    away_season_p = bgi.find_team_row(season_pitching, away_abbrev)
    away_trailing_p = bgi.find_team_row(trailing_pitching, away_abbrev)

    home_starter_xwoba = df.get_pitcher_xwoba_against(game["home_probable"], int(today[:4])) if game["home_probable"] else None
    away_starter_xwoba = df.get_pitcher_xwoba_against(game["away_probable"], int(today[:4])) if game["away_probable"] else None

    home_starter = bgi.build_starter(game["home_probable"], home_pitcher_hand, True,
                                      home_season_p, home_trailing_p,
                                      xwoba_against_season=home_starter_xwoba, xwoba_against_l30=home_starter_xwoba)
    away_starter = bgi.build_starter(game["away_probable"], away_pitcher_hand, False,
                                      away_season_p, away_trailing_p,
                                      xwoba_against_season=away_starter_xwoba, xwoba_against_l30=away_starter_xwoba)

    home_bullpen = bgi.build_bullpen(home_abbrev, season_pitching, trailing_pitching,
                                      xwoba_against_season=df.get_bullpen_xwoba_against(home_abbrev, int(today[:4])))
    away_bullpen = bgi.build_bullpen(away_abbrev, season_pitching, trailing_pitching,
                                      xwoba_against_season=df.get_bullpen_xwoba_against(away_abbrev, int(today[:4])))

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
