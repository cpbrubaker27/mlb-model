"""
MoundEdge-style MLB prediction model.

Turns team/pitcher/bullpen/park/weather inputs into:
  - projected score (both teams)
  - projected total
  - model win probability (moneyline)
  - edges vs market (ML, spread, total)
  - a Gold / Silver / Bronze / None confidence tier

All the weights below are named constants at the top so you can tune them
without touching the logic. Nothing here calls the network -- it's pure
math, meant to be fed clean inputs by a separate data-fetching script.
"""

from dataclasses import dataclass, field
from typing import Optional

# ----------------------------------------------------------------------
# TUNABLE CONSTANTS
# ----------------------------------------------------------------------

LEAGUE_AVG_RUNS_PER_GAME = 4.3      # MLB long-run average runs scored per team per game
LEAGUE_AVG_WRC_PLUS = 100.0         # by definition
LEAGUE_AVG_RA9 = 4.3                # runs allowed per 9, league average (mirrors runs/game)
LEAGUE_AVG_K_PCT = 0.22             # league average strikeout rate, used to scale K props
LEAGUE_AVG_XWOBA = 0.320            # league average xwOBA (both for hitters and xwOBA-against)
LEAGUE_AVG_CONTACT_PCT = 0.76       # league average contact rate, used to scale K props
LEAGUE_AVG_KBB_PCT = 0.15           # league average K% - BB% for pitchers

# How much weight recent form (last 30 games hitting / last 10-ish pitching)
# gets vs. full-season numbers. 0.5 = equal weight. Raise toward 1.0 to trust
# recent trends more; lower to trust the larger season sample more.
RECENT_WEIGHT_OFFENSE = 0.55
RECENT_WEIGHT_PITCHING = 0.50
RECENT_WEIGHT_BULLPEN = 0.55

# Innings split assumption: how much of the game the starter vs. bullpen
# is expected to cover. Shortened if the starter's recent trend is bad.
BASE_SP_INNINGS = 5.5
BASE_BP_INNINGS = 3.5

# Confidence tier thresholds
# ML edge = |model win% - market implied win%| (percentage points, 0-100 scale)
ML_GOLD_EDGE = 8.0
ML_SILVER_EDGE = 5.0
ML_BRONZE_EDGE = 2.5

# Total edge = |model total - market total| (runs)
TOTAL_GOLD_EDGE = 1.2
TOTAL_SILVER_EDGE = 0.7
TOTAL_BRONZE_EDGE = 0.35

# Spread edge = |model margin - market spread| (runs)
SPREAD_GOLD_EDGE = 1.5
SPREAD_SILVER_EDGE = 0.9
SPREAD_BRONZE_EDGE = 0.4

# Secondary ("xwOBA") model recency weights -- kept distinct from the
# primary model's weights so the cross-check varies on two axes (a
# different underlying metric AND a different recency emphasis), not just one.
TREND_RECENT_WEIGHT_OFFENSE = 0.85
TREND_RECENT_WEIGHT_PITCHING = 0.85
TREND_RECENT_WEIGHT_BULLPEN = 0.85

# How much a point of K-BB% above/below league average shifts the run
# environment. K-BB% is a cleaner, less-noisy read on a pitcher's current
# stuff than ERA, so it's used as a modifier on top of the xFIP-based rate
# rather than as a full standalone signal.
KBB_RUN_SENSITIVITY = 0.01   # 1% run adjustment per percentage point of K-BB% above/below league avg
KBB_MODIFIER_CAP = 0.08      # cap the total K-BB% adjustment at +/-8%

# How many percentage points money% must exceed bet% by to count as
# "sharp" action on a side (a classic reverse-line-movement style signal).
SHARP_DIVERGENCE_THRESHOLD = 10.0


# ----------------------------------------------------------------------
# INPUT DATA STRUCTURES
# ----------------------------------------------------------------------

@dataclass
class HandednessSplit:
    wrc_plus_season: float
    wrc_plus_l30: float


@dataclass
class TeamOffense:
    wrc_plus_season: float
    wrc_plus_l30: float
    vs_opp_hand: HandednessSplit  # team's wRC+ vs the opposing SP's throwing hand
    k_pct_season: Optional[float] = None   # team strikeout rate, e.g. 0.23 for 23%
    k_pct_l30: Optional[float] = None
    contact_pct_season: Optional[float] = None   # team contact rate on swings, e.g. 0.76
    contact_pct_l30: Optional[float] = None
    xwoba_season: Optional[float] = None   # Statcast expected wOBA -- powers the secondary model
    xwoba_l30: Optional[float] = None


@dataclass
class StarterPitcher:
    name: str
    throws: str            # "L" or "R"
    xfip_season: float
    xfip_l30: float
    home_era: Optional[float] = None
    road_era: Optional[float] = None
    is_home: bool = True
    recent_ip_trend_short: bool = False  # True if recently getting pulled early
    k9_season: Optional[float] = None
    k9_l30: Optional[float] = None
    k_bb_pct_season: Optional[float] = None   # K% - BB%, e.g. 0.18 for 18 points
    k_bb_pct_l30: Optional[float] = None
    xwoba_against_season: Optional[float] = None
    xwoba_against_l30: Optional[float] = None


@dataclass
class Bullpen:
    xfip_season: float
    xfip_l30: float
    xwoba_against_season: Optional[float] = None
    xwoba_against_l30: Optional[float] = None


@dataclass
class ParkWeather:
    park_factor: float          # 1.00 = neutral, >1 hitter-friendly, <1 pitcher-friendly
    weather_run_pct: float = 0.0  # additional +/- % from wind/temp, e.g. 0.05 = +5%


@dataclass
class MarketLines:
    home_ml: int             # American odds, e.g. -190
    away_ml: int              # e.g. +175
    total: float              # e.g. 9.5
    home_spread: float = -1.5
    home_spread_price: int = -130
    away_spread_price: int = 110


@dataclass
class TeamInputs:
    name: str
    offense: TeamOffense
    starter: StarterPitcher
    bullpen: Bullpen


@dataclass
class SharpMoneySplits:
    """Bet% vs money% splits. All fields optional -- pass None for any
    market you don't have data for, and it's simply excluded from tiering
    rather than penalized."""
    ml_home_bet_pct: Optional[float] = None
    ml_home_money_pct: Optional[float] = None
    ml_away_bet_pct: Optional[float] = None
    ml_away_money_pct: Optional[float] = None
    total_over_bet_pct: Optional[float] = None
    total_over_money_pct: Optional[float] = None
    total_under_bet_pct: Optional[float] = None
    total_under_money_pct: Optional[float] = None


@dataclass
class GameInputs:
    home: TeamInputs
    away: TeamInputs
    park_weather: ParkWeather
    market: MarketLines
    sharp: Optional[SharpMoneySplits] = None


# ----------------------------------------------------------------------
# STEP 1: BLEND RECENT + SEASON
# ----------------------------------------------------------------------

def blend(recent: float, season: float, recent_weight: float) -> float:
    return recent_weight * recent + (1 - recent_weight) * season


# ----------------------------------------------------------------------
# STEP 2: OFFENSE INDEX (relative to league average, adjusted for opposing hand)
# ----------------------------------------------------------------------

def offense_index(offense: TeamOffense, recent_weight: float = RECENT_WEIGHT_OFFENSE) -> float:
    overall = blend(offense.wrc_plus_l30, offense.wrc_plus_season, recent_weight)
    vs_hand = blend(
        offense.vs_opp_hand.wrc_plus_l30,
        offense.vs_opp_hand.wrc_plus_season,
        recent_weight,
    )
    # Give the handedness-specific split real but not total weight -- a small
    # sample vs. one hand shouldn't fully override the team's overall form.
    blended = 0.65 * vs_hand + 0.35 * overall
    return blended / LEAGUE_AVG_WRC_PLUS


# ----------------------------------------------------------------------
# STEP 3: RUN-PREVENTION INDEX (starter blended with bullpen, home/road aware)
# ----------------------------------------------------------------------

def kbb_modifier(k_bb_pct: Optional[float]) -> float:
    """Converts K-BB% into a small multiplier on run-prevention: better
    K-BB% (higher) reduces expected runs allowed a bit beyond what xFIP
    alone captures, since K-BB% reacts faster to a pitcher's current form."""
    if k_bb_pct is None:
        return 1.0
    diff = k_bb_pct - LEAGUE_AVG_KBB_PCT
    adj = max(-KBB_MODIFIER_CAP, min(KBB_MODIFIER_CAP, diff * KBB_RUN_SENSITIVITY * 100))
    return 1.0 - adj


def pitching_index(
    starter: StarterPitcher,
    bullpen: Bullpen,
    recent_weight_pitching: float = RECENT_WEIGHT_PITCHING,
    recent_weight_bullpen: float = RECENT_WEIGHT_BULLPEN,
) -> float:
    sp_xfip = blend(starter.xfip_l30, starter.xfip_season, recent_weight_pitching)

    # Nudge the starter's rate using home/road split if we have it, without
    # letting it swamp the xFIP-based estimate (xFIP is the sturdier signal).
    site_era = starter.home_era if starter.is_home else starter.road_era
    if site_era is not None:
        sp_rate = 0.7 * sp_xfip + 0.3 * site_era
    else:
        sp_rate = sp_xfip

    bp_rate = blend(bullpen.xfip_l30, bullpen.xfip_season, recent_weight_bullpen)

    sp_ip = BASE_SP_INNINGS - (1.0 if starter.recent_ip_trend_short else 0.0)
    bp_ip = 9.0 - sp_ip

    blended_ra9 = (sp_rate * sp_ip + bp_rate * bp_ip) / 9.0
    base_index = blended_ra9 / LEAGUE_AVG_RA9

    starter_kbb = blend(
        starter.k_bb_pct_l30 if starter.k_bb_pct_l30 is not None else starter.k_bb_pct_season,
        starter.k_bb_pct_season if starter.k_bb_pct_season is not None else starter.k_bb_pct_l30,
        recent_weight_pitching,
    ) if (starter.k_bb_pct_l30 is not None or starter.k_bb_pct_season is not None) else None

    return base_index * kbb_modifier(starter_kbb)


# ----------------------------------------------------------------------
# SECONDARY MODEL: xwOBA-based cross-check
# ----------------------------------------------------------------------
# This deliberately uses a *different* underlying metric than the primary
# model (xwOBA/xwOBA-against, driven by Statcast quality-of-contact data,
# rather than wRC+/xFIP). wRC+ reflects actual outcomes, which can be
# inflated or deflated by BABIP luck over a 30-day window; xwOBA strips
# that out. Requiring the two models to agree is a real cross-check
# precisely because they can disagree for a genuine reason (a team running
# hot on wRC+ but with mediocre xwOBA is exactly the kind of signal that
# should NOT get a Gold/Silver tier).

def offense_index_xwoba(offense: TeamOffense) -> Optional[float]:
    if offense.xwoba_season is None and offense.xwoba_l30 is None:
        return None
    xwoba = blend(
        offense.xwoba_l30 if offense.xwoba_l30 is not None else offense.xwoba_season,
        offense.xwoba_season if offense.xwoba_season is not None else offense.xwoba_l30,
        TREND_RECENT_WEIGHT_OFFENSE,
    )
    return xwoba / LEAGUE_AVG_XWOBA


def pitching_index_xwoba(starter: StarterPitcher, bullpen: Bullpen) -> Optional[float]:
    sp_xwoba = starter.xwoba_against_l30 if starter.xwoba_against_l30 is not None else starter.xwoba_against_season
    sp_xwoba_szn = starter.xwoba_against_season if starter.xwoba_against_season is not None else starter.xwoba_against_l30
    bp_xwoba = bullpen.xwoba_against_l30 if bullpen.xwoba_against_l30 is not None else bullpen.xwoba_against_season
    bp_xwoba_szn = bullpen.xwoba_against_season if bullpen.xwoba_against_season is not None else bullpen.xwoba_against_l30

    if sp_xwoba is None or bp_xwoba is None:
        return None

    sp_rate = blend(sp_xwoba, sp_xwoba_szn, TREND_RECENT_WEIGHT_PITCHING)
    bp_rate = blend(bp_xwoba, bp_xwoba_szn, TREND_RECENT_WEIGHT_BULLPEN)

    sp_ip = BASE_SP_INNINGS - (1.0 if starter.recent_ip_trend_short else 0.0)
    bp_ip = 9.0 - sp_ip
    blended_xwoba_against = (sp_rate * sp_ip + bp_rate * bp_ip) / 9.0

    # Lower xwOBA-against = better pitching = fewer runs, so this is inverted
    # relative to the offense index (higher xwOBA-against raises the index).
    base_index = blended_xwoba_against / LEAGUE_AVG_XWOBA

    starter_kbb = blend(
        starter.k_bb_pct_l30 if starter.k_bb_pct_l30 is not None else starter.k_bb_pct_season,
        starter.k_bb_pct_season if starter.k_bb_pct_season is not None else starter.k_bb_pct_l30,
        TREND_RECENT_WEIGHT_PITCHING,
    ) if (starter.k_bb_pct_l30 is not None or starter.k_bb_pct_season is not None) else None

    return base_index * kbb_modifier(starter_kbb)


# ----------------------------------------------------------------------
# STEP 4: PROJECTED RUNS
# ----------------------------------------------------------------------

def project_runs(off_idx: float, opp_pitch_idx: float, park_weather: ParkWeather) -> float:
    base = LEAGUE_AVG_RUNS_PER_GAME * off_idx * opp_pitch_idx
    env_multiplier = park_weather.park_factor * (1 + park_weather.weather_run_pct)
    return base * env_multiplier


# ----------------------------------------------------------------------
# STEP 5: WIN PROBABILITY (Pythagorean expectation, exponent ~1.83 for MLB)
# ----------------------------------------------------------------------

PYTHAG_EXP = 1.83

def win_probability(runs_for: float, runs_against: float) -> float:
    rf = max(runs_for, 0.1) ** PYTHAG_EXP
    ra = max(runs_against, 0.1) ** PYTHAG_EXP
    return rf / (rf + ra)


# ----------------------------------------------------------------------
# STRIKEOUT PROJECTION (for the "K prop to watch" slate summary)
# ----------------------------------------------------------------------

def project_strikeouts(starter: StarterPitcher, opp_offense: TeamOffense) -> Optional[float]:
    """Rough expected-Ks estimate: blended K/9 scaled by expected innings and
    by how the opponent's strikeout tendency compares to league average.
    Uses both K% and contact% when available (a low-contact, high-chase
    lineup should bump the projection beyond what K% alone captures);
    falls back to K% alone, then to no adjustment if neither is available.
    Returns None only if the pitcher has no K/9 data at all."""
    if starter.k9_season is None and starter.k9_l30 is None:
        return None
    k9 = blend(
        starter.k9_l30 if starter.k9_l30 is not None else starter.k9_season,
        starter.k9_season if starter.k9_season is not None else starter.k9_l30,
        RECENT_WEIGHT_PITCHING,
    )
    sp_ip = BASE_SP_INNINGS - (1.0 if starter.recent_ip_trend_short else 0.0)

    factors = []
    if opp_offense.k_pct_season is not None or opp_offense.k_pct_l30 is not None:
        opp_k_pct = blend(
            opp_offense.k_pct_l30 if opp_offense.k_pct_l30 is not None else opp_offense.k_pct_season,
            opp_offense.k_pct_season if opp_offense.k_pct_season is not None else opp_offense.k_pct_l30,
            RECENT_WEIGHT_OFFENSE,
        )
        factors.append(opp_k_pct / LEAGUE_AVG_K_PCT)
    if opp_offense.contact_pct_season is not None or opp_offense.contact_pct_l30 is not None:
        opp_contact_pct = blend(
            opp_offense.contact_pct_l30 if opp_offense.contact_pct_l30 is not None else opp_offense.contact_pct_season,
            opp_offense.contact_pct_season if opp_offense.contact_pct_season is not None else opp_offense.contact_pct_l30,
            RECENT_WEIGHT_OFFENSE,
        )
        # lower contact% = more swing-and-miss = higher K projection, so invert
        factors.append(LEAGUE_AVG_CONTACT_PCT / opp_contact_pct)

    opp_factor = sum(factors) / len(factors) if factors else 1.0
    return round(k9 * (sp_ip / 9.0) * opp_factor, 1)


# ----------------------------------------------------------------------
# STEP 6: MARKET IMPLIED PROBABILITY (de-vigged)
# ----------------------------------------------------------------------

def american_to_prob(odds: int) -> float:
    if odds < 0:
        return -odds / (-odds + 100)
    return 100 / (odds + 100)

def devig_two_way(prob_a: float, prob_b: float) -> tuple[float, float]:
    total = prob_a + prob_b
    return prob_a / total, prob_b / total


# ----------------------------------------------------------------------
# STEP 7: CONFIDENCE TIERING
# ----------------------------------------------------------------------

def tier_from_edge(edge: float, gold: float, silver: float, bronze: float) -> str:
    edge = abs(edge)
    if edge >= gold:
        return "gold"
    if edge >= silver:
        return "silver"
    if edge >= bronze:
        return "bronze"
    return "none"


_TIER_RANK = {"gold": 3, "silver": 2, "bronze": 1, "none": 0}
_TIER_BY_RANK = {v: k for k, v in _TIER_RANK.items()}


def gated_tier(
    raw_tier: str,
    models_agree: bool,
    sharp_side: Optional[str],
    model_pick_side: str,
) -> tuple[str, list[str]]:
    """
    Apply the agreement + sharp-money gates on top of a raw edge-based tier.

    Rules:
      - If the primary and secondary (trend) model don't agree on direction,
        cap at Bronze -- a big edge isn't trustworthy if the model disagrees
        with itself depending on how much you weight recent form.
      - If sharp money (money% notably exceeding bet%) is on the OTHER side
        from our pick, that's the classic signal you specifically said you
        want to respect -- it caps Gold/Silver entirely and knocks Bronze
        down to None.
      - If sharp money CONFIRMS our pick, it can bump a Bronze up to Silver
        (but never manufactures a Gold out of nothing).
      - If there's no sharp data at all, only the model-agreement gate applies.
    """
    notes: list[str] = []
    if raw_tier == "none":
        return "none", notes

    rank = _TIER_RANK[raw_tier]

    if not models_agree:
        notes.append("Primary and trend models disagree on direction -- capped at Bronze.")
        rank = min(rank, _TIER_RANK["bronze"])

    if sharp_side is not None:
        if sharp_side != model_pick_side:
            notes.append(
                f"Sharp money is on the other side ({sharp_side}) -- capping/demoting pick."
            )
            rank = min(rank, _TIER_RANK["bronze"])
            rank = max(rank - 1, _TIER_RANK["none"])
        else:
            if rank == _TIER_RANK["bronze"]:
                notes.append("Sharp money confirms the pick -- bumped Bronze to Silver.")
                rank = _TIER_RANK["silver"]

    return _TIER_BY_RANK[rank], notes


def sharp_side_for_ml(sharp: Optional[SharpMoneySplits], home_name: str, away_name: str) -> Optional[str]:
    if sharp is None:
        return None
    if sharp.ml_home_bet_pct is None or sharp.ml_home_money_pct is None:
        return None
    if sharp.ml_away_bet_pct is None or sharp.ml_away_money_pct is None:
        return None
    home_div = sharp.ml_home_money_pct - sharp.ml_home_bet_pct
    away_div = sharp.ml_away_money_pct - sharp.ml_away_bet_pct
    if home_div >= SHARP_DIVERGENCE_THRESHOLD and home_div > away_div:
        return f"{home_name} ML"
    if away_div >= SHARP_DIVERGENCE_THRESHOLD and away_div > home_div:
        return f"{away_name} ML"
    return None


def sharp_side_for_total(sharp: Optional[SharpMoneySplits]) -> Optional[str]:
    if sharp is None:
        return None
    if sharp.total_over_bet_pct is None or sharp.total_over_money_pct is None:
        return None
    if sharp.total_under_bet_pct is None or sharp.total_under_money_pct is None:
        return None
    over_div = sharp.total_over_money_pct - sharp.total_over_bet_pct
    under_div = sharp.total_under_money_pct - sharp.total_under_bet_pct
    if over_div >= SHARP_DIVERGENCE_THRESHOLD and over_div > under_div:
        return "Over"
    if under_div >= SHARP_DIVERGENCE_THRESHOLD and under_div > over_div:
        return "Under"
    return None


# ----------------------------------------------------------------------
# MAIN: RUN A FULL GAME
# ----------------------------------------------------------------------

@dataclass
class GamePrediction:
    home_runs: float
    away_runs: float
    total: float
    model_home_win_pct: float
    market_home_win_pct: float
    ml_edge_pts: float
    ml_pick: str
    ml_tier: str
    ml_notes: list[str]
    total_edge: float
    total_pick: str
    total_tier: str
    total_notes: list[str]
    spread_edge: float
    spread_pick: str
    spread_tier: str
    best_tier: str
    best_pick: str
    trend_home_runs: float
    trend_away_runs: float
    home_sp_k_proj: Optional[float] = None
    away_sp_k_proj: Optional[float] = None
    secondary_model: str = "xwoba"   # "xwoba" or "fallback" (recency-only) -- see run_game


def run_game(game: GameInputs) -> GamePrediction:
    # ---- Primary model ----
    home_off = offense_index(game.home.offense)
    away_off = offense_index(game.away.offense)
    home_pitch = pitching_index(game.home.starter, game.home.bullpen)
    away_pitch = pitching_index(game.away.starter, game.away.bullpen)

    home_runs = project_runs(home_off, away_pitch, game.park_weather)
    away_runs = project_runs(away_off, home_pitch, game.park_weather)
    total = home_runs + away_runs

    model_home_wp = win_probability(home_runs, away_runs)

    # ---- Secondary model: xwOBA-based cross-check ----
    # Uses a genuinely different metric (Statcast quality-of-contact) rather
    # than just a different recency weighting, so agreement between primary
    # and secondary is a real signal, not the same formula twice. Falls back
    # to a recency-reweighted variant of the primary model ONLY if xwOBA data
    # isn't available for this game yet (e.g. before that data source is wired
    # up) -- in that fallback case the "agreement" check is weaker, since it's
    # comparing two views of the same metric, so treat it as a placeholder
    # until xwOBA is flowing.
    trend_home_off_x = offense_index_xwoba(game.home.offense)
    trend_away_off_x = offense_index_xwoba(game.away.offense)
    trend_home_pitch_x = pitching_index_xwoba(game.home.starter, game.home.bullpen)
    trend_away_pitch_x = pitching_index_xwoba(game.away.starter, game.away.bullpen)

    using_xwoba_model = None not in (trend_home_off_x, trend_away_off_x, trend_home_pitch_x, trend_away_pitch_x)

    if using_xwoba_model:
        trend_home_runs = project_runs(trend_home_off_x, trend_away_pitch_x, game.park_weather)
        trend_away_runs = project_runs(trend_away_off_x, trend_home_pitch_x, game.park_weather)
    else:
        # Fallback: same formula as primary, just with heavier recency weighting.
        fb_home_off = offense_index(game.home.offense, TREND_RECENT_WEIGHT_OFFENSE)
        fb_away_off = offense_index(game.away.offense, TREND_RECENT_WEIGHT_OFFENSE)
        fb_home_pitch = pitching_index(
            game.home.starter, game.home.bullpen,
            TREND_RECENT_WEIGHT_PITCHING, TREND_RECENT_WEIGHT_BULLPEN,
        )
        fb_away_pitch = pitching_index(
            game.away.starter, game.away.bullpen,
            TREND_RECENT_WEIGHT_PITCHING, TREND_RECENT_WEIGHT_BULLPEN,
        )
        trend_home_runs = project_runs(fb_home_off, fb_away_pitch, game.park_weather)
        trend_away_runs = project_runs(fb_away_off, fb_home_pitch, game.park_weather)

    trend_total = trend_home_runs + trend_away_runs
    trend_home_wp = win_probability(trend_home_runs, trend_away_runs)

    # ---- Market ----
    market_home_raw = american_to_prob(game.market.home_ml)
    market_away_raw = american_to_prob(game.market.away_ml)
    market_home_wp, market_away_wp = devig_two_way(market_home_raw, market_away_raw)

    # ---- Moneyline ----
    ml_edge_pts = (model_home_wp - market_home_wp) * 100
    ml_pick = f"{game.home.name} ML" if ml_edge_pts > 0 else f"{game.away.name} ML"
    ml_raw_tier = tier_from_edge(ml_edge_pts, ML_GOLD_EDGE, ML_SILVER_EDGE, ML_BRONZE_EDGE)
    ml_models_agree = (model_home_wp >= 50) == (trend_home_wp >= 50)
    ml_sharp_side = sharp_side_for_ml(game.sharp, game.home.name, game.away.name)
    ml_tier, ml_notes = gated_tier(ml_raw_tier, ml_models_agree, ml_sharp_side, ml_pick)

    # ---- Total ----
    total_edge = total - game.market.total
    total_pick = "Over" if total_edge > 0 else "Under"
    total_raw_tier = tier_from_edge(total_edge, TOTAL_GOLD_EDGE, TOTAL_SILVER_EDGE, TOTAL_BRONZE_EDGE)
    total_models_agree = (total >= game.market.total) == (trend_total >= game.market.total)
    total_sharp_side = sharp_side_for_total(game.sharp)
    total_tier, total_notes = gated_tier(total_raw_tier, total_models_agree, total_sharp_side, total_pick)

    # ---- Spread (run line) -- gated on the same model-agreement check as ML,
    # since direction of margin tracks the same underlying projection.
    # No dedicated sharp-splits field for spreads is collected yet, so only
    # the model-agreement gate applies here.
    model_margin = home_runs - away_runs
    trend_margin = trend_home_runs - trend_away_runs
    market_home_margin = -game.market.home_spread
    spread_edge = model_margin - market_home_margin
    spread_pick = f"{game.home.name} {game.market.home_spread:+g}" if spread_edge > 0 else \
                  f"{game.away.name} {-game.market.home_spread:+g}"
    spread_raw_tier = tier_from_edge(spread_edge, SPREAD_GOLD_EDGE, SPREAD_SILVER_EDGE, SPREAD_BRONZE_EDGE)
    spread_models_agree = (model_margin >= market_home_margin) == (trend_margin >= market_home_margin)
    spread_tier, _ = gated_tier(spread_raw_tier, spread_models_agree, None, spread_pick)

    candidates = [(ml_tier, ml_pick), (total_tier, total_pick), (spread_tier, spread_pick)]
    best_tier, best_pick = max(candidates, key=lambda c: _TIER_RANK[c[0]])

    home_sp_k_proj = project_strikeouts(game.home.starter, game.away.offense)
    away_sp_k_proj = project_strikeouts(game.away.starter, game.home.offense)

    return GamePrediction(
        home_runs=round(home_runs, 1),
        away_runs=round(away_runs, 1),
        total=round(total, 1),
        model_home_win_pct=round(model_home_wp * 100, 1),
        market_home_win_pct=round(market_home_wp * 100, 1),
        ml_edge_pts=round(ml_edge_pts, 1),
        ml_pick=ml_pick,
        ml_tier=ml_tier,
        ml_notes=ml_notes,
        total_edge=round(total_edge, 2),
        total_pick=total_pick,
        total_tier=total_tier,
        total_notes=total_notes,
        spread_edge=round(spread_edge, 2),
        spread_pick=spread_pick,
        spread_tier=spread_tier,
        best_tier=best_tier,
        best_pick=best_pick,
        trend_home_runs=round(trend_home_runs, 1),
        trend_away_runs=round(trend_away_runs, 1),
        home_sp_k_proj=home_sp_k_proj,
        away_sp_k_proj=away_sp_k_proj,
        secondary_model="xwoba" if using_xwoba_model else "fallback",
    )
