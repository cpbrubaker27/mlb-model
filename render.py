"""
Renders the day's predictions into a single static HTML page.

Design direction: clean, minimal, light -- white/paper background, ink-navy
text, a condensed athletic display face for team names paired with a
monospace face for every stat (so columns of numbers actually line up like
a real box score), custom line icons, and tier badges styled like stamped
medals. Comparison rows (starters/hitting/bullpen) are tinted with each
team's real brand color at low opacity plus a colored left border, so you
can scan which row belongs to which team at a glance -- same idea as the
reference site highlighting Pittsburgh's row in Pirates yellow. Zero JS
framework -- vanilla CSS + a small inline <script> for the tier filter
buttons. Committed as docs/index.html and served free via GitHub Pages.
"""

import datetime as dt
from parks import TEAM_COLORS

EM_DASH = "\u2014"   # kept as a named constant -- Python <3.12 disallows backslash
                     # escapes inside f-string {} expressions, only in literal text

TIER_LABELS = {"gold": "GOLD", "silver": "SILVER", "bronze": "BRONZE", "none": ""}
TIER_STARS = {"gold": "\u2605\u2605\u2605", "silver": "\u2605\u2605", "bronze": "\u2605", "none": ""}

DEFAULT_TEAM_COLOR = "#6B7280"


def team_color(abbrev: str) -> str:
    return TEAM_COLORS.get(abbrev, DEFAULT_TEAM_COLOR)


def team_tint(abbrev: str, alpha_hex: str = "16") -> str:
    """Team color at low opacity for a row background wash. alpha_hex is a
    2-digit hex suffix (e.g. '16' \u2248 9% opacity, '10' \u2248 6%)."""
    return f"{team_color(abbrev)}{alpha_hex}"


# ----------------------------------------------------------------------
# DESIGN TOKENS
# ----------------------------------------------------------------------
# Clean paper-white background with charcoal-navy ink -- deliberately not
# the generic "cream + terracotta" AI default. The distinctive element here
# isn't the base palette (kept quiet and neutral on purpose) but the per-
# team color coding on every comparison row, which is driven by real
# content (each matchup's actual teams) rather than a fixed decorative
# accent.

CSS = """
@import url('https://fonts.googleapis.com/css2?family=Oswald:wght@500;600;700&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500;600&display=swap');

:root {
  color-scheme: light;
  --bg: #FAFAF9;
  --surface: #FFFFFF;
  --surface-2: #F4F5F6;
  --rule: #E5E7EA;
  --ink: #1B1F27;
  --ink-dim: #5B6472;
  --ink-faint: #8B93A0;
  --accent: #2A5CAA;
  --accent-bright: #3B72CC;
  --gold: #B8892B;
  --silver: #74808C;
  --bronze: #A0552C;
}
* { box-sizing: border-box; }
html {
  background: var(--bg);
  color-scheme: light;
}
body {
  background: var(--bg);
  color: var(--ink);
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  margin: 0;
  padding: 0 0 64px;
  -webkit-font-smoothing: antialiased;
  min-height: 100vh;
}
.num { font-family: 'JetBrains Mono', 'SF Mono', monospace; font-variant-numeric: tabular-nums; }

.header {
  padding: 22px 18px 16px;
  border-bottom: 1px solid var(--rule);
  position: sticky; top: 0; z-index: 10;
  background: var(--bg);
}
.header-top { display: flex; align-items: center; gap: 10px; }
.header h1 {
  margin: 0; font-family: 'Oswald', sans-serif; font-weight: 700;
  font-size: 1.5rem; letter-spacing: 0.02em; text-transform: uppercase;
  color: var(--ink);
}
.header h1 .accent { color: var(--accent-bright); }
.header .sub { color: var(--ink-dim); font-size: 0.8rem; margin-top: 5px; font-family: 'JetBrains Mono', monospace; }
.filters { display: flex; gap: 8px; margin-top: 14px; flex-wrap: wrap; }
.filter-btn {
  background: var(--surface); border: 1px solid var(--rule); color: var(--ink-dim);
  padding: 6px 14px; border-radius: 3px; font-size: 0.72rem; font-weight: 600;
  letter-spacing: 0.06em; text-transform: uppercase; cursor: pointer;
  font-family: 'Inter', sans-serif;
}
.filter-btn.active { background: var(--accent); border-color: var(--accent); color: #FFFFFF; }

.wrap { max-width: 760px; margin: 0 auto; padding: 18px; background: var(--bg); }

/* ---- Overview / scoreboard list ---- */
/* A flex-based row list instead of a rigid <table> -- lets each row's
   pieces (teams, time, projection, pick) wrap naturally on narrow phone
   screens instead of forcing horizontal scroll the way fixed table
   columns would. */
.overview-list { margin-bottom: 28px; background: var(--surface); border: 1px solid var(--rule); border-radius: 8px; overflow: hidden; }
.ov-row {
  display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between;
  gap: 6px 16px; padding: 12px 14px; border-bottom: 1px solid var(--rule);
  cursor: pointer; background: var(--surface);
}
.ov-row:last-child { border-bottom: none; }
.ov-row:hover { background: var(--surface-2); }
.ov-teams {
  display: flex; align-items: center; gap: 6px; font-family: 'Oswald', sans-serif;
  font-weight: 600; font-size: 0.95rem; letter-spacing: 0.01em;
}
.ov-teams .at { color: var(--ink-faint); font-weight: 500; margin: 0 2px; font-size: 0.8rem; }
.ov-meta { display: flex; flex-wrap: wrap; gap: 4px 12px; font-size: 0.76rem; color: var(--ink-dim); }
.ov-meta .num { color: var(--ink); }
.ov-pick { display: flex; align-items: center; gap: 8px; font-size: 0.85rem; font-weight: 500; margin-left: auto; }
.tierstar { color: var(--gold); letter-spacing: -1px; }

/* ---- Game card ---- */
.card {
  background: var(--surface); border: 1px solid var(--rule); border-radius: 8px;
  margin-bottom: 20px; overflow: hidden; scroll-margin-top: 130px; position: relative;
  box-shadow: 0 1px 3px rgba(20,24,32,0.06);
}
.card-head {
  display: flex; justify-content: space-between; align-items: flex-start;
  padding: 16px 18px; border-bottom: 1px solid var(--rule);
  background: var(--surface-2);
}
.masthead { display: flex; align-items: center; gap: 8px; }
.logo-badge { position: relative; width: 40px; height: 40px; flex-shrink: 0; }
.logo-badge.small { width: 22px; height: 22px; }
.logo-badge img { width: 100%; height: 100%; object-fit: contain; position: relative; z-index: 2; }
.logo-badge .fallback {
  display: none; position: absolute; inset: 0; border-radius: 50%;
  background: var(--team-color, var(--ink-faint)); color: #FFFFFF;
  align-items: center; justify-content: center;
  font-family: 'Oswald', sans-serif; font-weight: 700; font-size: 0.62rem;
  letter-spacing: 0.02em;
}
.logo-badge.small .fallback { font-size: 0.42rem; }
.masthead .vs { color: var(--ink-faint); font-family: 'Oswald', sans-serif; font-weight: 500; font-size: 1.1rem; margin: 0 2px; }
.matchup {
  font-family: 'Oswald', sans-serif; font-weight: 700; font-size: 1.35rem;
  letter-spacing: 0.01em; text-transform: uppercase; color: var(--ink);
}
.sp-line { color: var(--ink-dim); font-size: 0.78rem; margin-top: 5px; }
.time { color: var(--ink-dim); font-size: 0.75rem; font-family: 'JetBrains Mono', monospace; white-space: nowrap; }

/* ---- Medal / stamp tier badge ---- */
.medal {
  position: absolute; top: 14px; right: 16px; width: 46px; height: 46px;
  border-radius: 50%; display: flex; align-items: center; justify-content: center;
  transform: rotate(-8deg); border: 2px solid rgba(0,0,0,0.12);
  box-shadow: 0 2px 6px rgba(20,24,32,0.18), inset 0 1px 2px rgba(255,255,255,0.4);
}
.medal.gold { background: radial-gradient(circle at 35% 30%, #F0CC6B, var(--gold) 65%, #96701F); }
.medal.silver { background: radial-gradient(circle at 35% 30%, #E3E7E9, var(--silver) 65%, #5B6570); }
.medal.bronze { background: radial-gradient(circle at 35% 30%, #D08F5C, var(--bronze) 65%, #6E3E1D); }
.medal-label {
  font-family: 'Oswald', sans-serif; font-weight: 700; font-size: 0.55rem;
  letter-spacing: 0.03em; color: rgba(255,255,255,0.92); text-transform: uppercase;
}

.section { padding: 14px 18px; border-bottom: 1px solid var(--rule); }
.section:last-child { border-bottom: none; }
.section-head { display: flex; align-items: center; gap: 7px; margin-bottom: 9px; }
.section-head h3 {
  margin: 0; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.08em;
  color: var(--ink-faint); font-family: 'Inter', sans-serif; font-weight: 600;
}
.icon { width: 15px; height: 15px; flex-shrink: 0; color: var(--accent-bright); }

.row { display: flex; justify-content: space-between; align-items: center; font-size: 0.92rem; padding: 4px 0; }
.row .pick-label { font-weight: 500; }

table.stat { width: 100%; border-collapse: collapse; font-size: 0.82rem; background: var(--surface); }
table.stat th {
  text-align: left; color: var(--ink-faint); font-weight: 600; padding: 4px 8px;
  font-size: 0.65rem; text-transform: uppercase; letter-spacing: 0.04em; font-family: 'Inter', sans-serif;
  background: var(--surface);
}
/* Each team's row gets a subtle tint of their real brand color plus a
   colored left border, set inline per-row via CSS custom properties
   (team colors vary per matchup, so this can't be a static class) --
   same idea as highlighting a team's row in their own color, like
   Pirates yellow in the reference site. Custom properties set on <tr>
   inherit down to its <td> children, and cell backgrounds paint over
   row backgrounds in table layout, so the tint has to be applied at
   the td level (falling back to plain --surface when not set) rather
   than relying on the <tr> background alone. */
table.stat td { padding: 7px 8px; border-top: 1px solid var(--rule); background: var(--row-tint, var(--surface)); }
table.stat tr.team-row td:first-child {
  font-family: 'Oswald', sans-serif; font-weight: 600; letter-spacing: 0.01em;
  border-left: 3px solid var(--row-color, transparent); padding-left: 7px;
}

.pill {
  display: inline-flex; align-items: center; gap: 4px; padding: 3px 11px; border-radius: 3px;
  font-size: 0.68rem; font-weight: 700; letter-spacing: 0.05em; font-family: 'Inter', sans-serif;
}
.pill.gold { background: var(--gold); color: #FFFFFF; }
.pill.silver { background: var(--silver); color: #FFFFFF; }
.pill.bronze { background: var(--bronze); color: #FFFFFF; }
.pill.none { background: var(--surface-2); color: var(--ink-faint); border: 1px solid var(--rule); }

.notes { color: var(--bronze); font-size: 0.78rem; margin-top: 7px; padding-left: 18px; position: relative; }
.notes::before { content: "\u26a0"; position: absolute; left: 0; }
.proj { display: flex; gap: 20px; margin-bottom: 5px; }
.proj div { flex: 1; font-size: 1rem; }
.proj strong { font-family: 'JetBrains Mono', monospace; font-size: 1.1rem; }
.outlook { font-size: 0.87rem; color: var(--ink-dim); line-height: 1.5; }
.small { color: var(--ink-dim); font-size: 0.78rem; }
.envline { font-size: 0.85rem; color: var(--ink-dim); }
.envline .row-item { display: block; margin-bottom: 2px; }
.envline .row-item:last-child { margin-bottom: 0; }
.envline .num { color: var(--ink); }

/* ---- K prop leaderboard ---- */
.leaderboard { margin-top: 28px; }
.leaderboard-head { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.leaderboard-head h3 {
  margin: 0; font-size: 0.9rem; color: var(--ink); font-family: 'Oswald', sans-serif;
  font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em;
}
.lb-row {
  display: flex; align-items: center; gap: 12px; padding: 10px 12px;
  background: var(--surface); border: 1px solid var(--rule); border-radius: 6px; margin-bottom: 6px;
  border-left: 3px solid var(--row-color, var(--rule));
}
.lb-rank {
  font-family: 'Oswald', sans-serif; font-weight: 700; font-size: 1rem; color: var(--accent-bright);
  width: 20px; text-align: center;
}
.lb-name { font-family: 'Oswald', sans-serif; font-weight: 500; flex: 1; }
.lb-opp { color: var(--ink-faint); font-size: 0.8rem; }
.lb-k9 { color: var(--ink-dim); font-size: 0.8rem; }
.lb-proj { font-family: 'JetBrains Mono', monospace; font-weight: 600; color: var(--accent); font-size: 1rem; }
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
  document.querySelectorAll('.ov-row').forEach(row => {
    const best = row.getAttribute('data-best-tier');
    if (tier === 'all' || best === tier) { row.style.display = ''; }
    else { row.style.display = 'none'; }
  });
}
"""

# ----------------------------------------------------------------------
# ICONS -- small hand-drawn inline SVGs, stroke = currentColor, no
# external icon library needed for a single static page.
# ----------------------------------------------------------------------

def _icon(paths: str, view_box: str = "0 0 24 24") -> str:
    return (
        f'<svg class="icon" viewBox="{view_box}" fill="none" stroke="currentColor" '
        f'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">{paths}</svg>'
    )

ICON_WEATHER = _icon('<circle cx="12" cy="12" r="4.2"/><path d="M12 2.5v2.4M12 19.1v2.4M4.2 12H1.8M22.2 12h-2.4M5.6 5.6l1.7 1.7M16.7 16.7l1.7 1.7M18.4 5.6l-1.7 1.7M7.3 16.7l-1.7 1.7"/>')
ICON_PITCHER = _icon('<circle cx="12" cy="12" r="8.5"/><path d="M6 8.2c2.2 1.6 2.2 6 0 7.6M18 8.2c-2.2 1.6-2.2 6 0 7.6"/>')
ICON_BAT = _icon('<path d="M5 19 17.5 6.5a2.1 2.1 0 0 0-3-3L2 16 5 19Z"/><path d="M14 9.5l2.5-2.5"/>')
ICON_BULLPEN = _icon('<path d="M12 2.5c2.8 3.4 4.2 6 4.2 8.6a4.2 4.2 0 1 1-8.4 0c0-1 .3-1.9.9-2.9.4.9 1 1.4 1.7 1.4-.2-2.4.6-4.6 1.6-7.1Z"/>')
ICON_TARGET = _icon('<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4.3"/><circle cx="12" cy="12" r="0.6" fill="currentColor"/>')
ICON_MARKET = _icon('<circle cx="12" cy="12" r="8.5"/><path d="M12 6.5v11M15.2 9a3 2 0 0 0-3-1.6c-1.8 0-3 .9-3 2.1 0 3 6 1.4 6 4.3 0 1.2-1.3 2.2-3 2.2a3.2 3.2 0 0 1-3.2-1.9"/>')
ICON_FLAG = _icon('<path d="M5 21V4"/><path d="M5 4h13l-3 4 3 4H5"/>')


def build_logo_url(team_id: int) -> str:
    """MLB's official team-logo CDN, public and keyed by team ID (which we
    already have from the schedule fetch). SVG, no auth needed."""
    return f"https://www.mlbstatic.com/team-logos/{team_id}.svg"


def tier_pill(tier: str) -> str:
    if tier == "none":
        return ""
    return f'<span class="pill {tier}">{TIER_LABELS[tier]}</span>'


def tier_medal(tier: str) -> str:
    if tier == "none":
        return ""
    return f'<div class="medal {tier}"><span class="medal-label">{TIER_LABELS[tier]}</span></div>'


def _fmt_split(l30, season) -> str:
    if l30 is None and season is None:
        return f'<span class="num">{EM_DASH}</span>'
    l30_str = f"{l30:g}" if l30 is not None else EM_DASH
    season_str = f"{season:g}" if season is not None else EM_DASH
    return f"<span class='num'>{l30_str}</span> <span class='small'>({season_str} szn)</span>"


def build_game_outlook(away, home, prediction) -> str:
    """Auto-written 1-2 sentence summary: who has the scoring edge, and
    which way the run environment tilts."""
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


def _row_style(abbrev: str) -> str:
    """Inline style setting the CSS custom properties table.stat's team-row
    rule reads for background tint + left border color."""
    return f'style="--row-color:{team_color(abbrev)};--row-tint:{team_tint(abbrev)};"'


def render_game_card(meta: dict, home_inputs, away_inputs, park_weather, market, prediction) -> str:
    home = meta["home_abbrev"]
    away = meta["away_abbrev"]
    time_str = meta.get("time_str", "")
    home_team_id = meta.get("home_team_id")
    away_team_id = meta.get("away_team_id")
    game_id = f"g-{away}-{home}"

    if home_team_id and away_team_id:
        masthead = f"""
      <div class="masthead">
        <span class="logo-badge" style="--team-color:{team_color(away)};">
          <img src="{build_logo_url(away_team_id)}" alt="{away} logo" loading="lazy" onerror="this.style.display='none';this.nextElementSibling.style.display='flex';">
          <span class="fallback">{away}</span>
        </span>
        <span class="matchup">{away}</span>
        <span class="vs">@</span>
        <span class="logo-badge" style="--team-color:{team_color(home)};">
          <img src="{build_logo_url(home_team_id)}" alt="{home} logo" loading="lazy" onerror="this.style.display='none';this.nextElementSibling.style.display='flex';">
          <span class="fallback">{home}</span>
        </span>
        <span class="matchup">{home}</span>
      </div>"""
    else:
        masthead = f'<div class="masthead"><span class="matchup">{away}</span><span class="vs">@</span><span class="matchup">{home}</span></div>'

    ml_notes_html = "".join(f'<div class="notes">{n}</div>' for n in prediction.ml_notes)
    predicted_winner = home if prediction.model_home_win_pct >= 50 else away
    predicted_winner_pct = prediction.model_home_win_pct if prediction.model_home_win_pct >= 50 else round(100 - prediction.model_home_win_pct, 1)
    total_notes_html = "".join(f'<div class="notes">{n}</div>' for n in prediction.total_notes)

    park_label = "hitter-friendly" if park_weather.park_factor > 1.02 else \
                 "pitcher-friendly" if park_weather.park_factor < 0.98 else "neutral"
    env_pct = park_weather.weather_run_pct * 100
    env_dir = "helps runs \u25b2" if env_pct > 1 else "suppresses runs \u25bc" if env_pct < -1 else "neutral \u2192"

    starters_table = f"""
    <table class="stat">
      <tr><th>Pitcher</th><th>xwOBA-against (L30/SZN)</th><th>Home/Road ERA</th></tr>
      <tr class="team-row" {_row_style(away)}><td>{away_inputs.starter.name} ({away_inputs.starter.throws}HP)</td><td>{_fmt_split(away_inputs.starter.xwoba_against_l30, away_inputs.starter.xwoba_against_season)}</td>
          <td class="num">{away_inputs.starter.road_era if away_inputs.starter.road_era is not None else EM_DASH}</td></tr>
      <tr class="team-row" {_row_style(home)}><td>{home_inputs.starter.name} ({home_inputs.starter.throws}HP)</td><td>{_fmt_split(home_inputs.starter.xwoba_against_l30, home_inputs.starter.xwoba_against_season)}</td>
          <td class="num">{home_inputs.starter.home_era if home_inputs.starter.home_era is not None else EM_DASH}</td></tr>
    </table>"""

    hitting_table = f"""
    <table class="stat">
      <tr><th>Team</th><th>xwOBA (L30/SZN)</th><th>Runs/G (L30/SZN)</th></tr>
      <tr class="team-row" {_row_style(away)}><td>{away}</td><td>{_fmt_split(away_inputs.offense.xwoba_l30, away_inputs.offense.xwoba_season)}</td>
          <td>{_fmt_split(away_inputs.offense.runs_scored_pg_l30, away_inputs.offense.runs_scored_pg_season)}</td></tr>
      <tr class="team-row" {_row_style(home)}><td>{home}</td><td>{_fmt_split(home_inputs.offense.xwoba_l30, home_inputs.offense.xwoba_season)}</td>
          <td>{_fmt_split(home_inputs.offense.runs_scored_pg_l30, home_inputs.offense.runs_scored_pg_season)}</td></tr>
    </table>"""

    bullpen_table = f"""
    <table class="stat">
      <tr><th>Team</th><th>xwOBA-against (L30/SZN)</th></tr>
      <tr class="team-row" {_row_style(away)}><td>{away}</td><td>{_fmt_split(away_inputs.bullpen.xwoba_against_l30, away_inputs.bullpen.xwoba_against_season)}</td></tr>
      <tr class="team-row" {_row_style(home)}><td>{home}</td><td>{_fmt_split(home_inputs.bullpen.xwoba_against_l30, home_inputs.bullpen.xwoba_against_season)}</td></tr>
    </table>"""

    outlook = build_game_outlook(away, home, prediction)
    model_labels = {"xwoba": "xwOBA", "actual": "actual scoring rate"}
    if prediction.secondary_available:
        secondary_note = (
            f'<div class="small" style="margin-top:6px;">Primary model: {model_labels[prediction.primary_model]} '
            f'&middot; cross-check: {model_labels[prediction.secondary_model]}</div>'
        )
    else:
        secondary_note = (
            '<div class="notes">No cross-check model available for this game '
            '&mdash; tier capped conservatively.</div>'
        )

    medal_html = tier_medal(prediction.best_tier)

    return f"""
  <div class="card" id="{game_id}" data-best-tier="{prediction.best_tier}">
    {medal_html}
    <div class="card-head">
      <div>
        {masthead}
        <div class="sp-line">{away_inputs.starter.name} ({away_inputs.starter.throws}HP) vs {home_inputs.starter.name} ({home_inputs.starter.throws}HP)</div>
      </div>
      <div class="time">{time_str}</div>
    </div>
    <div class="section">
      <div class="section-head">{ICON_WEATHER}<h3>Weather &amp; Park</h3></div>
      <div class="envline">
        <div class="row-item">Park: <span class="num">{park_label} ({park_weather.park_factor:.2f}\u00d7)</span></div>
        <div class="row-item">Effect: {env_dir} <span class="num">({env_pct:+.0f}%)</span></div>
      </div>
    </div>
    <div class="section">
      <div class="section-head">{ICON_PITCHER}<h3>Starting Pitchers</h3></div>
      {starters_table}
    </div>
    <div class="section">
      <div class="section-head">{ICON_BAT}<h3>Hitting</h3></div>
      {hitting_table}
    </div>
    <div class="section">
      <div class="section-head">{ICON_BULLPEN}<h3>Bullpen</h3></div>
      {bullpen_table}
    </div>
    <div class="section">
      <div class="section-head">{ICON_TARGET}<h3>Game Summary &amp; Prediction</h3></div>
      <div class="proj">
        <div>{away}: <strong class="num">{prediction.away_runs}</strong> <span class="small">(trend {prediction.trend_away_runs})</span></div>
        <div>{home}: <strong class="num">{prediction.home_runs}</strong> <span class="small">(trend {prediction.trend_home_runs})</span></div>
      </div>
      <div class="small">Model total <span class="num">{prediction.total}</span> &middot; Market <span class="num">{market.total}</span> &middot; <span class="num">{market.away_ml:+d}/{market.home_ml:+d}</span> ML</div>
      <div class="outlook" style="margin-top:8px;">{outlook}</div>
      {secondary_note}
    </div>
    <div class="section">
      <div class="section-head">{ICON_MARKET}<h3>Moneyline</h3></div>
      <div class="small">Model favors <strong>{predicted_winner}</strong> to win (<span class="num">{predicted_winner_pct}%</span>)</div>
      <div class="row" style="margin-top:6px;"><span class="pick-label">Value pick: {prediction.ml_pick}</span>{tier_pill(prediction.ml_tier)}</div>
      <div class="small">Market implies <span class="num">{prediction.market_home_win_pct}%</span> for {home} vs model's <span class="num">{prediction.model_home_win_pct}%</span> &middot; edge <span class="num">{prediction.ml_edge_pts:+.1f}</span> pts</div>
      {ml_notes_html}
    </div>
    <div class="section">
      <div class="section-head">{ICON_TARGET}<h3>Total</h3></div>
      <div class="row"><span class="pick-label">{prediction.total_pick} {market.total}</span>{tier_pill(prediction.total_tier)}</div>
      <div class="small">edge <span class="num">{prediction.total_edge:+.2f}</span> runs</div>
      {total_notes_html}
    </div>
    <div class="section">
      <div class="section-head">{ICON_FLAG}<h3>Run Line</h3></div>
      <div class="row"><span class="pick-label">{prediction.spread_pick}</span>{tier_pill(prediction.spread_tier)}</div>
      <div class="small">edge <span class="num">{prediction.spread_edge:+.2f}</span> runs</div>
    </div>
  </div>
"""


def render_overview_row(meta: dict, market, prediction) -> str:
    home, away = meta["home_abbrev"], meta["away_abbrev"]
    home_team_id = meta.get("home_team_id")
    away_team_id = meta.get("away_team_id")
    game_id = f"g-{away}-{home}"
    star = TIER_STARS.get(prediction.best_tier, "")

    if home_team_id and away_team_id:
        teams_html = (
            f'<span class="logo-badge small" style="--team-color:{team_color(away)};">'
            f'<img src="{build_logo_url(away_team_id)}" alt="{away}" loading="lazy" onerror="this.style.display=\'none\';this.nextElementSibling.style.display=\'flex\';">'
            f'<span class="fallback">{away}</span></span>{away}'
            f'<span class="at">@</span>'
            f'<span class="logo-badge small" style="--team-color:{team_color(home)};">'
            f'<img src="{build_logo_url(home_team_id)}" alt="{home}" loading="lazy" onerror="this.style.display=\'none\';this.nextElementSibling.style.display=\'flex\';">'
            f'<span class="fallback">{home}</span></span>{home}'
        )
    else:
        teams_html = f'{away}<span class="at">@</span>{home}'

    return f"""<div class="ov-row" data-best-tier="{prediction.best_tier}" onclick="document.getElementById('{game_id}').scrollIntoView({{behavior:'smooth'}})">
      <div class="ov-teams">{teams_html}</div>
      <div class="ov-meta">
        <span class="num">{meta.get('time_str','')}</span>
        <span class="num">{prediction.away_runs}\u2013{prediction.home_runs} ({prediction.total})</span>
        <span>Mkt <span class="num">{market.total}</span></span>
      </div>
      <div class="ov-pick"><span>{prediction.best_pick}</span><span class="tierstar">{star}</span></div>
    </div>"""


def render_k_prop_watch(k_prop_rows: list[dict]) -> str:
    """k_prop_rows: list of dicts with keys: pitcher, team, opp, k9, k_proj.
    Rendered as a ranked leaderboard, best strikeout-prop candidates first."""
    rows_with_k = [r for r in k_prop_rows if r["k_proj"] is not None]
    if not rows_with_k:
        return ""
    rows_with_k.sort(key=lambda r: r["k_proj"], reverse=True)
    top = rows_with_k[:5]
    rows_html = "\n".join(
        f"""<div class="lb-row" style="--row-color:{team_color(r['team'])};">
          <div class="lb-rank">{i+1}</div>
          <div class="lb-name">{r['pitcher']} <span class="lb-opp">{r['team']} vs {r['opp']}</span></div>
          <div class="lb-k9">{r['k9']:.1f} K/9</div>
          <div class="lb-proj">{r['k_proj']:.1f}</div>
        </div>"""
        for i, r in enumerate(top)
    )
    return f"""
    <div class="leaderboard">
      <div class="leaderboard-head">{ICON_TARGET}<h3>Strikeout Watch</h3></div>
      {rows_html}
    </div>"""


def render_page(date_str: str, overview_rows: list[str], game_cards: list[str], k_prop_html: str = "") -> str:
    cards_html = "\n".join(game_cards)
    overview_html = "\n".join(overview_rows)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<title>MLB Daily Model &mdash; {date_str}</title>
<style>{CSS}</style>
</head>
<body>
  <div class="header">
    <div class="header-top">
      {ICON_TARGET}
      <h1>MLB Daily <span class="accent">Model</span></h1>
    </div>
    <div class="sub">{date_str} &middot; generated {dt.datetime.utcnow().strftime('%H:%M UTC')}</div>
    <div class="filters">
      <button class="filter-btn active" id="f-all" onclick="filterCards('all')">All</button>
      <button class="filter-btn" id="f-gold" onclick="filterCards('gold')">Gold</button>
      <button class="filter-btn" id="f-silver" onclick="filterCards('silver')">Silver</button>
      <button class="filter-btn" id="f-bronze" onclick="filterCards('bronze')">Bronze</button>
    </div>
  </div>
  <div class="wrap">
    <div class="overview-list">
      {overview_html}
    </div>
    {cards_html}
    {k_prop_html}
  </div>
  <script>{JS}</script>
</body>
</html>
"""
