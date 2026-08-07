"""
Static park data: run-scoring park factor, lat/long (for weather lookup),
and roof type. Park factors are slow-moving (recalculated ~yearly by
FanGraphs/Statcast) so a static table refreshed once a season is fine --
no need to fetch this live every day.

park_factor: 1.00 = neutral. >1.00 = hitter-friendly, <1.00 = pitcher-friendly.
Values below are illustrative multi-year averages; replace with the current
season's published factors (FanGraphs "Guts!" page) at the start of each season.

roof: "open" (outdoor, weather matters), "fixed" (dome, weather doesn't apply),
"retractable" (check game-day status -- treat as fixed/closed unless you have
a live roof-status source; defaults to "closed" behavior for safety).
"""

PARKS = {
    "ARI": {"team": "Diamondbacks", "venue": "Chase Field", "lat": 33.4455, "lon": -112.0667, "park_factor": 1.02, "roof": "retractable"},
    "ATL": {"team": "Braves", "venue": "Truist Park", "lat": 33.8908, "lon": -84.4678, "park_factor": 0.99, "roof": "open"},
    "BAL": {"team": "Orioles", "venue": "Oriole Park at Camden Yards", "lat": 39.2838, "lon": -76.6217, "park_factor": 1.02, "roof": "open"},
    "BOS": {"team": "Red Sox", "venue": "Fenway Park", "lat": 42.3467, "lon": -71.0972, "park_factor": 1.06, "roof": "open"},
    "CHC": {"team": "Cubs", "venue": "Wrigley Field", "lat": 41.9484, "lon": -87.6553, "park_factor": 1.00, "roof": "open"},
    "CHW": {"team": "White Sox", "venue": "Rate Field", "lat": 41.8299, "lon": -87.6338, "park_factor": 1.02, "roof": "open"},
    "CIN": {"team": "Reds", "venue": "Great American Ball Park", "lat": 39.0975, "lon": -84.5071, "park_factor": 1.08, "roof": "open"},
    "CLE": {"team": "Guardians", "venue": "Progressive Field", "lat": 41.4962, "lon": -81.6852, "park_factor": 0.97, "roof": "open"},
    "COL": {"team": "Rockies", "venue": "Coors Field", "lat": 39.7559, "lon": -104.9942, "park_factor": 1.14, "roof": "open"},
    "DET": {"team": "Tigers", "venue": "Comerica Park", "lat": 42.3390, "lon": -83.0485, "park_factor": 0.96, "roof": "open"},
    "HOU": {"team": "Astros", "venue": "Daikin Park", "lat": 29.7573, "lon": -95.3555, "park_factor": 0.98, "roof": "retractable"},
    "KCR": {"team": "Royals", "venue": "Kauffman Stadium", "lat": 39.0517, "lon": -94.4803, "park_factor": 1.01, "roof": "open"},
    "LAA": {"team": "Angels", "venue": "Angel Stadium", "lat": 33.8003, "lon": -117.8827, "park_factor": 0.97, "roof": "open"},
    "LAD": {"team": "Dodgers", "venue": "Dodger Stadium", "lat": 34.0739, "lon": -118.2400, "park_factor": 0.96, "roof": "open"},
    "MIA": {"team": "Marlins", "venue": "loanDepot Park", "lat": 25.7781, "lon": -80.2196, "park_factor": 0.93, "roof": "retractable"},
    "MIL": {"team": "Brewers", "venue": "American Family Field", "lat": 43.0280, "lon": -87.9712, "park_factor": 0.99, "roof": "retractable"},
    "MIN": {"team": "Twins", "venue": "Target Field", "lat": 44.9817, "lon": -93.2776, "park_factor": 0.98, "roof": "open"},
    "NYM": {"team": "Mets", "venue": "Citi Field", "lat": 40.7571, "lon": -73.8458, "park_factor": 0.96, "roof": "open"},
    "NYY": {"team": "Yankees", "venue": "Yankee Stadium", "lat": 40.8296, "lon": -73.9262, "park_factor": 1.03, "roof": "open"},
    "OAK": {"team": "Athletics", "venue": "Sutter Health Park", "lat": 38.5802, "lon": -121.5153, "park_factor": 1.00, "roof": "open"},
    "ATH": {"team": "Athletics", "venue": "Sutter Health Park", "lat": 38.5802, "lon": -121.5153, "park_factor": 1.00, "roof": "open"},
    "PHI": {"team": "Phillies", "venue": "Citizens Bank Park", "lat": 39.9061, "lon": -75.1665, "park_factor": 1.03, "roof": "open"},
    "PIT": {"team": "Pirates", "venue": "PNC Park", "lat": 40.4469, "lon": -80.0057, "park_factor": 0.95, "roof": "open"},
    "SDP": {"team": "Padres", "venue": "Petco Park", "lat": 32.7073, "lon": -117.1566, "park_factor": 0.95, "roof": "open"},
    "SEA": {"team": "Mariners", "venue": "T-Mobile Park", "lat": 47.5914, "lon": -122.3325, "park_factor": 0.93, "roof": "retractable"},
    "SFG": {"team": "Giants", "venue": "Oracle Park", "lat": 37.7786, "lon": -122.3893, "park_factor": 0.92, "roof": "open"},
    "STL": {"team": "Cardinals", "venue": "Busch Stadium", "lat": 38.6226, "lon": -90.1928, "park_factor": 0.97, "roof": "open"},
    "TBR": {"team": "Rays", "venue": "George M. Steinbrenner Field", "lat": 27.9803, "lon": -82.5065, "park_factor": 1.01, "roof": "open"},
    "TEX": {"team": "Rangers", "venue": "Globe Life Field", "lat": 32.7473, "lon": -97.0824, "park_factor": 0.99, "roof": "retractable"},
    "TOR": {"team": "Blue Jays", "venue": "Rogers Centre", "lat": 43.6414, "lon": -79.3894, "park_factor": 1.01, "roof": "retractable"},
    "WSN": {"team": "Nationals", "venue": "Nationals Park", "lat": 38.8730, "lon": -77.0074, "park_factor": 1.00, "roof": "open"},
}

# The MLB Stats API's live "abbreviation" field sometimes differs from the
# more familiar/older short codes (confirmed by an actual failed run: the
# API returned "ATH" for the Athletics, "WSH" for the Nationals, "CWS" for
# the White Sox, etc., none of which matched the keys above). Rather than
# guess every current code, alias the common variants to the same entry so
# a lookup succeeds regardless of which style the API hands back that day.
_ALIASES = {
    "ATH": "OAK", "WSH": "WSN", "CWS": "CHW", "KC": "KCR",
    "AZ": "ARI", "SD": "SDP", "SF": "SFG", "TB": "TBR",
}
for _alias, _canonical in _ALIASES.items():
    if _canonical in PARKS and _alias not in PARKS:
        PARKS[_alias] = PARKS[_canonical]

TEAM_COLORS = {
    "ARI": "#A71930", "ATL": "#CE1141", "BAL": "#DF4601", "BOS": "#BD3039",
    "CHC": "#0E3386", "CHW": "#27251F", "CIN": "#C6011F", "CLE": "#E31937",
    "COL": "#33006F", "DET": "#0C2C56", "HOU": "#EB6E1F", "KCR": "#004687",
    "LAA": "#BA0021", "LAD": "#005A9C", "MIA": "#00A3E0", "MIL": "#FFC52F",
    "MIN": "#002B5C", "NYM": "#002D72", "NYY": "#003087", "OAK": "#003831", "ATH": "#003831",
    "PHI": "#E81828", "PIT": "#FDB827", "SDP": "#FFC425", "SEA": "#0C2C56",
    "SFG": "#FD5A1E", "STL": "#C41E3A", "TBR": "#092C5C", "TEX": "#003278",
    "TOR": "#134A8E", "WSN": "#AB0003",
}
for _alias, _canonical in _ALIASES.items():
    if _canonical in TEAM_COLORS and _alias not in TEAM_COLORS:
        TEAM_COLORS[_alias] = TEAM_COLORS[_canonical]
