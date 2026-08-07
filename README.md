# MLB Daily Model

Automated daily MLB predictions (ML, spread, total) with Gold/Silver/Bronze
confidence tiers, published as a free static page via GitHub Pages.

## What's here

- `model.py` — the scoring engine (projections, win %, edges, tiering). No network calls.
- `parks.py` — static park factor + coordinates table.
- `data_fetch.py` — pulls schedule, stats, weather, odds. Bet splits currently return `None` (see note below).
- `build_game_inputs.py` — glue layer that turns raw fetched data into `model.py`'s input objects.
- `render.py` — generates the HTML page.
- `main.py` — daily entry point, run by the GitHub Actions workflow.
- `.github/workflows/daily.yml` — runs `main.py` every morning and publishes `docs/index.html` via GitHub Pages.

## One-time setup

1. **Create a GitHub repo** and push this folder to it.
2. **Get a free Odds API key**: sign up at https://the-odds-api.com (free tier = 500 requests/month, plenty for one daily pull).
3. **Add the key as a repo secret**: Settings → Secrets and variables → Actions → New repository secret → name it `ODDS_API_KEY`.
4. **Enable GitHub Pages**: Settings → Pages → Source → "GitHub Actions".
5. **Trigger a first run manually**: Actions tab → "Daily MLB Model" → Run workflow. Don't wait for the cron — run it once by hand so we can see and fix whatever breaks.

## What will almost certainly need a fix on the first real run

I built this without network access in my sandbox, so nothing here has hit
the live APIs yet. Most likely trouble spots, in rough order of likelihood:

1. **`pybaseball` column names** (`build_game_inputs.py`, `data_fetch.py`) — FanGraphs table column names can shift slightly between `pybaseball` versions (e.g. `"wRC+"` vs `"wRC_plus"`). If you get a `KeyError`, this is where to look.
2. **Team abbreviation mismatches** — MLB Stats API, FanGraphs, and The Odds API each use slightly different team naming (e.g. `CWS` vs `CHW`, full names vs abbreviations). `find_team_row()` and `_match_odds_event()` have partial fixes but may need more entries.
3. **vs-hand batting splits** — `get_team_batting_vs_hand()` is a stub (raises `NotImplementedError`). Until it's wired up, the model falls back to each team's overall wRC+ instead of their split vs. that day's starter's throwing hand — the model still runs, just with one less refinement.
4. **Starter throwing hand** — currently hardcoded to `"R"` in `main.py`. Needs a lookup against the MLB Stats API `/people/{id}` endpoint.
5. **xERA** — best-effort via Statcast; if unavailable it's just not used (the model runs fine on xFIP alone, which is the primary signal anyway).

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
