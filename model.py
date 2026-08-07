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
LEAGUE_AVG_K_PCT = 0.22             # league average strikeout rate, used to scale K props
LEAGUE_AVG_XWOBA = 0.320            # league average xwOBA (both for hitters and xwOBA-against)
LEAGUE_AVG_CONTACT_PCT = 0.76       # league average contact rate, used to scale K props
LEAGUE_AVG_KBB_PCT = 0.15           # league average K% - BB% for pitchers

# How much weight recent form (last 30 days) gets vs. full-season numbers,
# for the primary (xwOBA-based) model. 0.5 = equal weight.
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

# Secondary ("actual scoring rate") model recency weights -- kept distinct
# from the primary model's weights so the two models vary on two axes (a
# different underlying metric AND a different recency emphasis).
SECONDARY_RECENT_WEIGHT = 0.85

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
class TeamOffense:
    """Note: despite the name, this also carries the team's own run-prevention
    numbers (runs_allowed_pg) -- that's what powers the secondary model's
    pitching side, since actual runs allowed already blends starter + bullpen
    + defense into one real-world number, unlike the primary model which
    needs starter/bullpen split separately for the xwOBA-against calc."""
    xwoba_season: Optional[float] = None      # Statcast expected wOBA -- primary model's offense engine
    xwoba_l30: Optional[float] = None
    runs_scored_pg_season: Optional[float] = None   # actual runs/game -- secondary model's offense engine
    runs_scored_pg_l30: Optional[float] = None
    runs_allowed_pg_season: Optional[float] = None  # actual runs allowed/game -- secondary model's pitching engine
    runs_allowed_pg_l30: Optional[float] = None
    k_pct_season: Optional[float] = None      # team strikeout rate, e.g. 0.23 for 23% -- K-prop watch only
    k_pct_l30: Optional[float] = None
    contact_pct_season: Optional[float] = None   # team contact rate on swings -- K-prop watch only
    contact_pct_l30: Optional[float] = None


@dataclass
class StarterPitcher:
    name: str
    throws: str            # "L" or "R"
    xwoba_against_season: Optional[float] = None   # primary model's pitching engine
    xwoba_against_l30: Optional[float] = None
    home_era: Optional[float] = None
    road_era: Optional[float] = None
    is_home: bool = True
    recent_ip_trend_short: bool = False  # True if recently getting pulled early
    k9_season: Optional[float] = None
    k9_l30: Optional[float] = None
    k_bb_pct_season: Optional[float] = None   # K% - BB%, e.g. 0.18 for 18 points
    k_bb_pct_l30: Optional[float] = None


@dataclass
class Bullpen:
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
# PRIMARY MODEL: xwOBA-based (Statcast quality-of-contact)
# ----------------------------------------------------------------------
# xwOBA/xwOBA-against are Baseball Savant's park/luck-adjusted, quality-of-
# contact-based offense/pitching metrics. They're used here instead of
# wRC+/xFIP (FanGraphs) purely because FanGraphs blocks automated requests
# from cloud-hosted IPs (like GitHub Actions runners), while Baseball
# Savant's public CSV endpoints are built for exactly this kind of use.
# xwOBA strips out BABIP-style luck the way wRC+ can't, so if anything this
# is a stronger forward-looking input, not a downgrade.

def kbb_modifier(k_bb_pct: Optional[float]) -> float:
    """Converts K-BB% into a small multiplier on run-prevention: better
    K-BB% (higher) reduces expected runs allowed a bit beyond what
    xwOBA-against alone captures, since K-BB% reacts faster to a pitcher's
    current form."""
    if k_bb_pct is None:
        return 1.0
    diff = k_bb_pct - LEAGUE_AVG_KBB_PCT
    adj = max(-KBB_MODIFIER_CAP, min(KBB_MODIFIER_CAP, diff * KBB_RUN_SENSITIVITY * 100))
    return 1.0 - adj


def _blended_kbb(starter: StarterPitcher, recent_weight: float) -> Optional[float]:
    if starter.k_bb_pct_l30 is None and starter.k_bb_pct_season is None:
        return None
    return blend(
        starter.k_bb_pct_l30 if starter.k_bb_pct_l30 is not None else starter.k_bb_pct_season,
        starter.k_bb_pct_season if starter.k_bb_pct_season is not None else starter.k_bb_pct_l30,
        recent_weight,
    )


def offense_index_xwoba(offense: TeamOffense, recent_weight: float = RECENT_WEIGHT_OFFENSE) -> Optional[float]:
    if offense.xwoba_season is None and offense.xwoba_l30 is None:
        return None
    xwoba = blend(
        offense.xwoba_l30 if offense.xwoba_l30 is not None else offense.xwoba_season,
        offense.xwoba_season if offense.xwoba_season is not None else offense.xwoba_l30,
        recent_weight,
    )
    return xwoba / LEAGUE_AVG_XWOBA


def pitching_index_xwoba(
    starter: StarterPitcher,
    bullpen: Bullpen,
    recent_weight_pitching: float = RECENT_WEIGHT_PITCHING,
    recent_weight_bullpen: float = RECENT_WEIGHT_BULLPEN,
) -> Optional[float]:
    if starter.xwoba_against_season is None and starter.xwoba_against_l30 is None:
        return None
    if bullpen.xwoba_against_season is None and bullpen.xwoba_against_l30 is None:
        return None

    sp_rate = blend(
        starter.xwoba_against_l30 if starter.xwoba_against_l30 is not None else starter.xwoba_against_season,
        starter.xwoba_against_season if starter.xwoba_against_season is not None else starter.xwoba_against_l30,
        recent_weight_pitching,
    )
    bp_rate = blend(
        bullpen.xwoba_against_l30 if bullpen.xwoba_against_l30 is not None else bullpen.xwoba_against_season,
        bullpen.xwoba_against_season if bullpen.xwoba_against_season is not None else bullpen.xwoba_against_l30,
        recent_weight_bullpen,
    )

    sp_ip = BASE_SP_INNINGS - (1.0 if starter.recent_ip_trend_short else 0.0)
    bp_ip = 9.0 - sp_ip
    blended_xwoba_against = (sp_rate * sp_ip + bp_rate * bp_ip) / 9.0

    # Lower xwOBA-against = better pitching = fewer runs, so this is inverted
    # relative to the offense index (higher xwOBA-against raises the index).
    base_index = blended_xwoba_against / LEAGUE_AVG_XWOBA
    return base_index * kbb_modifier(_blended_kbb(starter, recent_weight_pitching))


# ----------------------------------------------------------------------
# SECONDARY MODEL: actual team scoring rate
# ----------------------------------------------------------------------
# Uses real runs scored/allowed per game (MLB Stats API -- standings/team
# stats, never blocked) instead of any expected-outcome metric. This is a
# genuinely different lens from the xwOBA primary model: "what has this
# matchup profile actually produced" vs. "what does quality of contact
# predict." Requiring the two to agree is a real cross-check specifically
# because they measure different things and can genuinely diverge.

def offense_index_actual(offense: TeamOffense, recent_weight: float = SECONDARY_RECENT_WEIGHT) -> Optional[float]:
    if offense.runs_scored_pg_season is None and offense.runs_scored_pg_l30 is None:
        return None
    rate = blend(
        offense.runs_scored_pg_l30 if offense.runs_scored_pg_l30 is not None else offense.runs_scored_pg_season,
        offense.runs_scored_pg_season if offense.runs_scored_pg_season is not None else offense.runs_scored_pg_l30,
        recent_weight,
    )
    return rate / LEAGUE_AVG_RUNS_PER_GAME


def pitching_index_actual(offense: TeamOffense, recent_weight: float = SECONDARY_RECENT_WEIGHT) -> Optional[float]:
    """Takes a TeamOffense because that's where the team's own runs-allowed
    numbers live (see the TeamOffense docstring) -- actual runs allowed
    already blends starter + bullpen + defense into one real number, so no
    separate starter/bullpen split is needed the way xwOBA-against needs."""
    if offense.runs_allowed_pg_season is None and offense.runs_allowed_pg_l30 is None:
        return None
    rate = blend(
        offense.runs_allowed_pg_l30 if offense.runs_allowed_pg_l30 is not None else offense.runs_allowed_pg_season,
        offense.runs_allowed_pg_season if offense.runs_allowed_pg_season is not None else offense.runs_allowed_pg_l30,
        recent_weight,
    )
    return rate / LEAGUE_AVG_RUNS_PER_GAME


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
    primary_model: str = "xwoba"      # "xwoba" or "actual" -- whichever ended up as primary
    secondary_model: str = "actual"   # the other one, used as the cross-check
    secondary_available: bool = True  # False means no real cross-check was possible this game


def run_game(game: GameInputs) -> GamePrediction:
    # ---- Try the xwOBA model ----
    xwoba_home_off = offense_index_xwoba(game.home.offense)
    xwoba_away_off = offense_index_xwoba(game.away.offense)
    xwoba_home_pitch = pitching_index_xwoba(game.home.starter, game.home.bullpen)
    xwoba_away_pitch = pitching_index_xwoba(game.away.starter, game.away.bullpen)
    xwoba_ready = None not in (xwoba_home_off, xwoba_away_off, xwoba_home_pitch, xwoba_away_pitch)

    # ---- Try the actual-scoring-rate model ----
    actual_home_off = offense_index_actual(game.home.offense)
    actual_away_off = offense_index_actual(game.away.offense)
    actual_home_pitch = pitching_index_actual(game.home.offense)
    actual_away_pitch = pitching_index_actual(game.away.offense)
    actual_ready = None not in (actual_home_off, actual_away_off, actual_home_pitch, actual_away_pitch)

    # xwOBA is preferred as primary (it's the more forward-looking metric),
    # but if for some reason it's unavailable for this game, actual-scoring
    # steps in as primary rather than failing the whole projection.
    if xwoba_ready:
        home_off, away_off, home_pitch, away_pitch = xwoba_home_off, xwoba_away_off, xwoba_home_pitch, xwoba_away_pitch
        primary_model = "xwoba"
    elif actual_ready:
        home_off, away_off, home_pitch, away_pitch = actual_home_off, actual_away_off, actual_home_pitch, actual_away_pitch
        primary_model = "actual"
    else:
        raise ValueError(
            "Neither xwOBA nor actual-scoring-rate data is available for this game -- "
            "cannot project without at least one of the two data sources."
        )

    home_runs = project_runs(home_off, away_pitch, game.park_weather)
    away_runs = project_runs(away_off, home_pitch, game.park_weather)
    total = home_runs + away_runs
    model_home_wp = win_probability(home_runs, away_runs)

    # ---- Secondary model: whichever one ISN'T primary, if it's available ----
    if primary_model == "xwoba" and actual_ready:
        trend_home_runs = project_runs(actual_home_off, actual_away_pitch, game.park_weather)
        trend_away_runs = project_runs(actual_away_off, actual_home_pitch, game.park_weather)
        secondary_model, secondary_available = "actual", True
    elif primary_model == "actual" and xwoba_ready:
        trend_home_runs = project_runs(xwoba_home_off, xwoba_away_pitch, game.park_weather)
        trend_away_runs = project_runs(xwoba_away_off, xwoba_home_pitch, game.park_weather)
        secondary_model, secondary_available = "xwoba", True
    else:
        # No real cross-check possible -- mirror primary so downstream math
        # doesn't break, but models_agree gets forced False below so tiers
        # are capped rather than trusting an edge with no cross-check at all.
        trend_home_runs, trend_away_runs = home_runs, away_runs
        secondary_model, secondary_available = "none", False

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
    ml_sharp_side = sharp_side_for_ml(game.sharp, game.home.name, game.away.name)
    if secondary_available:
        ml_models_agree = (model_home_wp >= 50) == (trend_home_wp >= 50)
        ml_tier, ml_notes = gated_tier(ml_raw_tier, ml_models_agree, ml_sharp_side, ml_pick)
    else:
        ml_tier, ml_notes = gated_tier(ml_raw_tier, True, ml_sharp_side, ml_pick)
        ml_tier = _TIER_BY_RANK[min(_TIER_RANK[ml_tier], _TIER_RANK["bronze"])]
        ml_notes.append("No cross-check model available for this game -- tier capped conservatively.")

    # ---- Total ----
    total_edge = total - game.market.total
    total_pick = "Over" if total_edge > 0 else "Under"
    total_raw_tier = tier_from_edge(total_edge, TOTAL_GOLD_EDGE, TOTAL_SILVER_EDGE, TOTAL_BRONZE_EDGE)
    total_sharp_side = sharp_side_for_total(game.sharp)
    if secondary_available:
        total_models_agree = (total >= game.market.total) == (trend_total >= game.market.total)
        total_tier, total_notes = gated_tier(total_raw_tier, total_models_agree, total_sharp_side, total_pick)
    else:
        total_tier, total_notes = gated_tier(total_raw_tier, True, total_sharp_side, total_pick)
        total_tier = _TIER_BY_RANK[min(_TIER_RANK[total_tier], _TIER_RANK["bronze"])]
        total_notes.append("No cross-check model available for this game -- tier capped conservatively.")

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
    if secondary_available:
        spread_models_agree = (model_margin >= market_home_margin) == (trend_margin >= market_home_margin)
        spread_tier, _ = gated_tier(spread_raw_tier, spread_models_agree, None, spread_pick)
    else:
        spread_tier, _ = gated_tier(spread_raw_tier, True, None, spread_pick)
        spread_tier = _TIER_BY_RANK[min(_TIER_RANK[spread_tier], _TIER_RANK["bronze"])]

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
        primary_model=primary_model,
        secondary_model=secondary_model,
        secondary_available=secondary_available,
    )
