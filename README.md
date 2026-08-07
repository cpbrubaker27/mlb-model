# MLB Daily Model

Automated daily MLB predictions (ML, spread, total) with Gold/Silver/Bronze
confidence tiers, published as a free static page via GitHub Pages.

## What's here

- `model.py` — the scoring engine. **Primary model**: xwOBA/xwOBA-against (Baseball Savant). **Secondary/cross-check model**: actual team runs scored/allowed per game (MLB Stats API). A pick only reaches Gold/Silver if both models agree on direction. No network calls in this file.
- `parks.py` — static park factor + coordinates table.
- `data_fetch.py` — pulls schedule, team scoring rates, pitcher K/BB counts, and records from the MLB Stats API; xwOBA from Baseball Savant via pybaseball; weather; odds. Bet splits currently return `None` (see note below).
- `build_game_inputs.py` — glue layer that turns raw fetched data into `model.py`'s input objects.
- `render.py` — generates the HTML page.
- `main.py` — daily entry point, run by the GitHub Actions workflow.
- `.github/workflows/daily.yml` — runs `main.py` every morning and publishes `docs/index.html` via GitHub Pages.

## Why not FanGraphs?

The original build used FanGraphs (wRC+, xFIP) for the primary model. FanGraphs blocks requests from cloud-hosted IPs — including GitHub Actions runners — with a 403, and that block held even after adding browser-like headers and retries. Baseball Savant's CSV-export endpoints (used here via `pybaseball`) and the MLB Stats API are both built for this kind of programmatic access and haven't shown the same issue. xwOBA is arguably a *stronger* forward-looking input than wRC+ anyway, since it's based on quality of contact rather than actual outcomes (which can be inflated/deflated by BABIP luck over a 30-day window) — so this wasn't just a workaround, it's a reasonable model upgrade.

## One-time setup

1. **Create a GitHub repo** and push this folder to it.
2. **Get a free Odds API key**: sign up at https://the-odds-api.com (free tier = 500 requests/month, plenty for one daily pull).
3. **Add the key as a repo secret**: Settings → Secrets and variables → Actions → New repository secret → name it `ODDS_API_KEY`.
4. **Enable GitHub Pages**: Settings → Pages → Source → "GitHub Actions".
5. **Trigger a first run manually**: Actions tab → "Daily MLB Model" → Run workflow. Don't wait for the cron — run it once by hand so we can see and fix whatever breaks.

## What will almost certainly need a fix on the first real run

I built this without network access in my sandbox, so nothing here has hit
the live APIs yet (except the isolated bug fixes we already found and
patched together, like the f-string syntax error and the FanGraphs block).
Most likely trouble spots, in rough order of likelihood:

1. **Baseball Savant / pybaseball column names** (`data_fetch.py`'s xwOBA functions) — `pybaseball`'s exact column names (`est_woba`, `team_abbrev`, etc.) can shift slightly between versions. If you get a `KeyError`, this is where to look.
2. **Team abbreviation mismatches** — MLB Stats API and The Odds API use slightly different team naming conventions. `_match_odds_event()` in `main.py` matches by team name substring, which is more robust than abbreviation matching but still worth checking if a game gets silently skipped.
3. **Starter throwing hand** — currently hardcoded to `"R"` in `main.py`. Needs a lookup against the MLB Stats API `/people/{id}` endpoint.
4. **If Baseball Savant also ends up blocked** — unlikely (Savant's endpoints are built for this), but if you see 403s from Savant the same way we saw them from FanGraphs, the model still runs fine using actual-scoring-rate as primary (it degrades gracefully, per-game) — but you'd lose the intended xwOBA-first design point. The fix at that point is the same one we discussed for FanGraphs: run the fetch step from a non-cloud IP.

None of these break the architecture — they're the normal first-run
punch list for any new data pipeline. Send me the error output from the
first manual run and I'll patch them directly.

## Bet splits / sharp money

There's no reliable free API for bet% vs money% splits. `get_bet_splits()`
returns `None` for now, which the model handles gracefully — it just skips
the sharp-money gate for that game rather than guessing. If you get access
to a source (a paid splits API, or a specific site to scrape), tell me and
I'll wire it in — the model side is already built to use it the moment
it's available.

## Tuning

All the model's weights and tier thresholds live at the top of `model.py`
as named constants (how much to trust recent form vs. season stats, how
big an edge needs to be for Bronze/Silver/Gold, the sharp-money divergence
threshold). Change those, don't touch the logic below them.
