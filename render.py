"""
Renders the day's predictions into a single static HTML page, styled close
to the MoundEdge reference: a top summary table + filterable, expandable
game cards with weather/park, team form, starting pitchers, hitting,
bullpen, and an auto-written game outlook.

Still zero JS framework -- vanilla CSS + a small inline <script> for the
tier filter buttons. Committed as docs/index.html and served free via
GitHub Pages.
"""

import datetime as dt

TIER_COLORS = {"gold": "#d4af37", "silver": "#9ea7b3", "bronze": "#b5651d", "none": "#3a3f4a"}
TIER_STARS = {"gold": "\u2605\u2605\u2605", "silver": "\u2605\u2605", "bronze": "\u2605", "none": ""}

CSS = """
:root { color-scheme: dark; }
* { box-sizing: border-box; }
body { background:#0f1115; color:#e7e9ec; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; margin:0; padding:0 0 60px; }
.header { padding:20px 16px 14px; border-bottom:1px solid #23262e; position:sticky; top:0; background:#0f1115; z-index:10; }
.header h1 { margin:0; font-size:1.35rem; }
.header .sub { color:#9aa1ab; font-size:0.82rem; margin-top:4px; }
.filters { display:flex; gap:8px; margin-top:12px; flex-wrap:wrap; }
.filter-btn { background:#1b1e26; border:1px solid #2a2e38; color:#c7cbd3; padding:6px 14px; border-radius:20px; font-size:0.8rem; cursor:pointer; }
.filter-btn.active { background:#2c2f38; border-color:#4a4f5c; color:#fff; }
.wrap { max-width:760px; margin:0 auto; padding:16px; }
table.overview { width:100%; border-collapse:collapse; margin-bottom:24px; font-size:0.85rem; }
table.overview th { text-align:left; color:#8a91a0; font-weight:600; padding:6px 8px; border-bottom:1px solid #262a35; text-transform:uppercase; font-size:0.68rem; letter-spacing:0.03em; }
table.overview td { padding:8px; border-bottom:1px solid #1c1f27; }
table.overview tr { cursor:pointer; }
table.overview tr:hover td { background:#161922; }
.tierstar { color:#d4af37; }
.card { background:#161922; border:1px solid #262a35; border-radius:12px; margin-bottom:18px; overflow:hidden; scroll-margin-top:120px; }
.card-head { display:flex; justify-content:space-between; align-items:center; padding:12px 16px; border-bottom:1px solid #262a35; }
.matchup { font-weight:700; font-size:1.05rem; }
.time { color:#9aa1ab; font-size:0.8rem; }
.section { padding:12px 16px; border-bottom:1px solid #1e2129; }
.section:last-child { border-bottom:none; }
.section h3 { margin:0 0 8px; font-size:0.75rem; text-transform:uppercase; letter-spacing:0.04em; color:#8a91a0; }
.row { display:flex; justify-content:space-between; font-size:0.9rem; padding:3px 0; }
table.stat { width:100%; border-collapse:collapse; font-size:0.82rem; }
table.stat th { text-align:left; color:#8a91a0; font-weight:600; padding:4px 6px; font-size:0.68rem; text-transform:uppercase; }
table.stat td { padding:4px 6px; border-top:1px solid #1e2129; }
.pill { display:inline-block; padding:3px 10px; border-radius:20px; font-size:0.72rem; font-weight:700; letter-spacing:0.03em; }
.notes { color:#c6a15b; font-size:0.8rem; margin-top:6px; }
.contra { color:#e0645c; font-weight:600; }
.proj { display:flex; gap:18px; margin-bottom:4px; }
.proj div { flex:1; font-size:0.95rem; }
.outlook { font-size:0.87rem; color:#c7cbd3; line-height:1.4; }
.small { color:#9aa1ab; font-size:0.78rem; }
.envline { font-size:0.85rem; color:#c7cbd3; }
"""

JS = """
function filterCards(tier) {
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('f-' + tier).classList.add('active');
  document.querySelectorAll('.card').forEach(card => {
    const best = card.getAttribute('data-best-tier');
    if (tier === 'all' || best === tier) { card.style.display = ''; }
    else { card.style.display = 'none'; }
  });
  document.querySelectorAll('table.overview tbody tr').forEach(row => {
    const best = row.getAttribute('data-best-tier');
    if (tier === 'all' || best === tier) { row.style.display = ''; }
    else { row.style.display = 'none'; }
  });
}
"""


def tier_pill(tier: str) -> str:
    if tier == "none":
        return ""
    color = TIER_COLORS[tier]
    return f'<span class="pill" style="background:{color};color:#0f1115;">{tier.upper()}</span>'


def _fmt_split(l30: float, season: float) -> str:
    return f"{l30:g} <span class='small'>({season:g} szn)</span>"


def build_game_outlook(away, home, prediction) -> str:
    """Auto-written 1-2 sentence summary in the MoundEdge style: who has the
    scoring edge, and which way the run environment tilts."""
    parts = []
    if prediction.away_runs > prediction.home_runs:
        parts.append(f"{away} projects for the higher-scoring day at the plate.")
    elif prediction.home_runs > prediction.away_runs:
        parts.append(f"{home} projects for the higher-scoring day at the plate.")
    parts.append(
        f"Run environment tilts toward the {prediction.total_pick.lower()} "
        f"({prediction.total} model total, {prediction.total_edge:+.2f} edge vs market)."
    )
    if prediction.ml_tier != "none":
        parts.append(f"Best-supported side: {prediction.ml_pick}.")
    return " ".join(parts)


def render_game_card(meta: dict, home_inputs, away_inputs, park_weather, market, prediction) -> str:
    home = meta["home_abbrev"]
    away = meta["away_abbrev"]
    time_str = meta.get("time_str", "")
    game_id = f"g-{away}-{home}"

    ml_notes_html = "".join(f'<div class="notes contra">{n}</div>' for n in prediction.ml_notes)
    total_notes_html = "".join(f'<div class="notes contra">{n}</div>' for n in prediction.total_notes)

    park_label = "hitter-friendly" if park_weather.park_factor > 1.02 else \
                 "pitcher-friendly" if park_weather.park_factor < 0.98 else "neutral"
    env_pct = park_weather.weather_run_pct * 100
    env_dir = "helps runs \u25b2" if env_pct > 1 else "suppresses runs \u25bc" if env_pct < -1 else "neutral \u2192"

    starters_table = f"""
    <table class="stat">
      <tr><th>Pitcher</th><th>xFIP (L30/SZN)</th><th>Home/Road ERA</th></tr>
      <tr><td>{away_inputs.starter.name} ({away_inputs.starter.throws}HP)</td><td>{_fmt_split(away_inputs.starter.xfip_l30, away_inputs.starter.xfip_season)}</td>
          <td>{away_inputs.starter.road_era if away_inputs.starter.road_era is not None else '\u2014'}</td></tr>
      <tr><td>{home_inputs.starter.name} ({home_inputs.starter.throws}HP)</td><td>{_fmt_split(home_inputs.starter.xfip_l30, home_inputs.starter.xfip_season)}</td>
          <td>{home_inputs.starter.home_era if home_inputs.starter.home_era is not None else '\u2014'}</td></tr>
    </table>"""

    hitting_table = f"""
    <table class="stat">
      <tr><th>Team</th><th>wRC+ (L30/SZN)</th><th>vs Opp Hand (L30/SZN)</th></tr>
      <tr><td>{away}</td><td>{_fmt_split(away_inputs.offense.wrc_plus_l30, away_inputs.offense.wrc_plus_season)}</td>
          <td>{_fmt_split(away_inputs.offense.vs_opp_hand.wrc_plus_l30, away_inputs.offense.vs_opp_hand.wrc_plus_season)}</td></tr>
      <tr><td>{home}</td><td>{_fmt_split(home_inputs.offense.wrc_plus_l30, home_inputs.offense.wrc_plus_season)}</td>
          <td>{_fmt_split(home_inputs.offense.vs_opp_hand.wrc_plus_l30, home_inputs.offense.vs_opp_hand.wrc_plus_season)}</td></tr>
    </table>"""

    bullpen_table = f"""
    <table class="stat">
      <tr><th>Team</th><th>xFIP (L30/SZN)</th></tr>
      <tr><td>{away}</td><td>{_fmt_split(away_inputs.bullpen.xfip_l30, away_inputs.bullpen.xfip_season)}</td></tr>
      <tr><td>{home}</td><td>{_fmt_split(home_inputs.bullpen.xfip_l30, home_inputs.bullpen.xfip_season)}</td></tr>
    </table>"""

    outlook = build_game_outlook(away, home, prediction)
    secondary_note = "" if prediction.secondary_model == "xwoba" else \
        '<div class="small" style="margin-top:4px;">\u26a0 xwOBA data unavailable for this game \u2014 cross-check used recency-only fallback model.</div>'

    return f"""
  <div class="card" id="{game_id}" data-best-tier="{prediction.best_tier}">
    <div class="card-head">
      <div>
        <div class="matchup">{away} @ {home} <span class="tierstar">{TIER_STARS.get(prediction.best_tier, '')}</span></div>
        <div class="small">SP: {away_inputs.starter.name} ({away_inputs.starter.throws}HP) vs {home_inputs.starter.name} ({home_inputs.starter.throws}HP)</div>
      </div>
      <div class="time">{time_str}</div>
    </div>
    <div class="section">
      <h3>Weather &amp; Park</h3>
      <div class="envline">Park: {park_label} ({park_weather.park_factor:.2f}\u00d7) &middot; Effect: {env_dir} ({env_pct:+.0f}%)</div>
    </div>
    <div class="section">
      <h3>Starting Pitchers</h3>
      {starters_table}
    </div>
    <div class="section">
      <h3>Hitting</h3>
      {hitting_table}
    </div>
    <div class="section">
      <h3>Bullpen</h3>
      {bullpen_table}
    </div>
    <div class="section">
      <h3>Game Summary &amp; Prediction</h3>
      <div class="proj">
        <div>{away}: <strong>{prediction.away_runs}</strong> <span class="small">(trend {prediction.trend_away_runs})</span></div>
        <div>{home}: <strong>{prediction.home_runs}</strong> <span class="small">(trend {prediction.trend_home_runs})</span></div>
      </div>
      <div class="small">Model total {prediction.total} &middot; Market {market.total} &middot; {market.away_ml:+d}/{market.home_ml:+d} ML</div>
      <div class="outlook" style="margin-top:8px;">{outlook}</div>
      {secondary_note}
    </div>
    <div class="section">
      <h3>Moneyline</h3>
      <div class="row"><span>{prediction.ml_pick}</span>{tier_pill(prediction.ml_tier)}</div>
      <div class="small">Model {prediction.model_home_win_pct}% vs Market {prediction.market_home_win_pct}% (home) &middot; edge {prediction.ml_edge_pts:+.1f} pts</div>
      {ml_notes_html}
    </div>
    <div class="section">
      <h3>Total</h3>
      <div class="row"><span>{prediction.total_pick} {market.total}</span>{tier_pill(prediction.total_tier)}</div>
      <div class="small">edge {prediction.total_edge:+.2f} runs</div>
      {total_notes_html}
    </div>
    <div class="section">
      <h3>Run Line</h3>
      <div class="row"><span>{prediction.spread_pick}</span>{tier_pill(prediction.spread_tier)}</div>
      <div class="small">edge {prediction.spread_edge:+.2f} runs</div>
    </div>
  </div>
"""


def render_overview_row(meta: dict, market, prediction) -> str:
    home, away = meta["home_abbrev"], meta["away_abbrev"]
    game_id = f"g-{away}-{home}"
    star = TIER_STARS.get(prediction.best_tier, "")
    return f"""<tr data-best-tier="{prediction.best_tier}" onclick="document.getElementById('{game_id}').scrollIntoView({{behavior:'smooth'}})">
      <td>{away} @ {home}</td>
      <td>{meta.get('time_str','')}</td>
      <td>{prediction.away_runs}\u2013{prediction.home_runs} <span class="small">({prediction.total})</span></td>
      <td>{market.total}</td>
      <td>{prediction.best_pick}</td>
      <td class="tierstar">{star}</td>
    </tr>"""


def render_k_prop_watch(k_prop_rows: list[dict]) -> str:
    """k_prop_rows: list of dicts with keys: pitcher, team, opp, k9, k_proj.
    Shows the single best strikeout-prop candidate across the slate (highest
    projected Ks), same spirit as the example site's Strikeout Watch section."""
    rows_with_k = [r for r in k_prop_rows if r["k_proj"] is not None]
    if not rows_with_k:
        return ""
    rows_with_k.sort(key=lambda r: r["k_proj"], reverse=True)
    top = rows_with_k[:5]
    body = "\n".join(
        f"""<tr><td>{r['pitcher']}</td><td>{r['team']}</td><td>{r['opp']}</td>
            <td>{r['k9']:.1f}</td><td><strong>{r['k_proj']:.1f}</strong></td></tr>"""
        for r in top
    )
    return f"""
    <h3 style="margin:24px 0 8px; font-size:0.9rem; color:#8a91a0; text-transform:uppercase; letter-spacing:0.04em;">Strikeout Watch</h3>
    <table class="overview">
      <thead><tr><th>Pitcher</th><th>Team</th><th>Opp</th><th>K/9 (blend)</th><th>Proj Ks</th></tr></thead>
      <tbody>{body}</tbody>
    </table>"""


def render_page(date_str: str, overview_rows: list[str], game_cards: list[str], k_prop_html: str = "") -> str:
    cards_html = "\n".join(game_cards)
    overview_html = "\n".join(overview_rows)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MLB Daily Model &mdash; {date_str}</title>
<style>{CSS}</style>
</head>
<body>
  <div class="header">
    <h1>MLB Daily Model</h1>
    <div class="sub">{date_str} &middot; generated {dt.datetime.utcnow().strftime('%H:%M UTC')}</div>
    <div class="filters">
      <button class="filter-btn active" id="f-all" onclick="filterCards('all')">All</button>
      <button class="filter-btn" id="f-gold" onclick="filterCards('gold')">Gold</button>
      <button class="filter-btn" id="f-silver" onclick="filterCards('silver')">Silver</button>
      <button class="filter-btn" id="f-bronze" onclick="filterCards('bronze')">Bronze</button>
    </div>
  </div>
  <div class="wrap">
    <table class="overview">
      <thead><tr><th>Game</th><th>Time</th><th>Projected</th><th>Market</th><th>Best Pick</th><th>Tier</th></tr></thead>
      <tbody>
        {overview_html}
      </tbody>
    </table>
    {cards_html}
    {k_prop_html}
  </div>
  <script>{JS}</script>
</body>
</html>
"""
