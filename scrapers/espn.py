"""Height and weight from ESPN's public roster API.

The ratings sites do not all publish body measurements: maddenratings has
neither height nor weight, fcratings has neither, and 2kratings has height but
no weight. theshowratings has both, so baseball needs nothing from here.

ESPN exposes a roster per team as JSON with `displayHeight` and `weight`, which
is one request per team -- 94 in total. Output goes to data/espn_bio.json and is
merged onto the ratings datasets by scrapers/merge.py.
"""

from __future__ import annotations

import json

from .common import (DATA_DIR, Fetcher, log, parse_height_inches, to_int)

ROSTER = ("https://site.api.espn.com/apis/site/v2/sports/{path}/teams/"
          "{team}/roster")

# maddenratings slug -> ESPN team abbreviation
NFL_TEAMS = {
    "arizona-cardinals": "ari", "atlanta-falcons": "atl",
    "baltimore-ravens": "bal", "buffalo-bills": "buf",
    "carolina-panthers": "car", "chicago-bears": "chi",
    "cincinnati-bengals": "cin", "cleveland-browns": "cle",
    "dallas-cowboys": "dal", "denver-broncos": "den", "detroit-lions": "det",
    "green-bay-packers": "gb", "houston-texans": "hou",
    "indianapolis-colts": "ind", "jacksonville-jaguars": "jax",
    "kansas-city-chiefs": "kc", "las-vegas-raiders": "lv",
    "los-angeles-chargers": "lac", "los-angeles-rams": "lar",
    "miami-dolphins": "mia", "minnesota-vikings": "min",
    "new-england-patriots": "ne", "new-orleans-saints": "no",
    "new-york-giants": "nyg", "new-york-jets": "nyj",
    "philadelphia-eagles": "phi", "pittsburgh-steelers": "pit",
    "san-francisco-49ers": "sf", "seattle-seahawks": "sea",
    "tampa-bay-buccaneers": "tb", "tennessee-titans": "ten",
    "washington-commanders": "wsh",
}

# 2kratings slug -> ESPN team abbreviation
NBA_TEAMS = {
    "atlanta-hawks": "atl", "boston-celtics": "bos", "brooklyn-nets": "bkn",
    "charlotte-hornets": "cha", "chicago-bulls": "chi",
    "cleveland-cavaliers": "cle", "dallas-mavericks": "dal",
    "denver-nuggets": "den", "detroit-pistons": "det",
    "golden-state-warriors": "gs", "houston-rockets": "hou",
    "indiana-pacers": "ind", "los-angeles-clippers": "lac",
    "los-angeles-lakers": "lal", "memphis-grizzlies": "mem",
    "miami-heat": "mia", "milwaukee-bucks": "mil",
    "minnesota-timberwolves": "min", "new-orleans-pelicans": "no",
    "new-york-knicks": "ny", "oklahoma-city-thunder": "okc",
    "orlando-magic": "orl", "philadelphia-76ers": "phi", "phoenix-suns": "phx",
    "portland-trail-blazers": "por", "sacramento-kings": "sac",
    "san-antonio-spurs": "sa", "toronto-raptors": "tor", "utah-jazz": "utah",
    "washington-wizards": "wsh",
}

# Our club name -> (ESPN league path, ESPN team id). ESPN names both Milan
# clubs correctly, unlike fcratings, so these are keyed on the real club.
SOCCER_CLUBS = {
    "Arsenal": ("soccer/eng.1", "359"),
    "Aston Villa": ("soccer/eng.1", "362"),
    "Chelsea": ("soccer/eng.1", "363"),
    "Liverpool": ("soccer/eng.1", "364"),
    "Manchester City": ("soccer/eng.1", "382"),
    "Manchester United": ("soccer/eng.1", "360"),
    "Newcastle": ("soccer/eng.1", "361"),
    "Tottenham": ("soccer/eng.1", "367"),
    "Athletic Bilbao": ("soccer/esp.1", "93"),
    "Atletico Madrid": ("soccer/esp.1", "1068"),
    "Barcelona": ("soccer/esp.1", "83"),
    "Real Madrid": ("soccer/esp.1", "86"),
    "Bayer Leverkusen": ("soccer/ger.1", "131"),
    "Bayern Munich": ("soccer/ger.1", "132"),
    "Borussia Dortmund": ("soccer/ger.1", "124"),
    "RB Leipzig": ("soccer/ger.1", "11420"),
    "Stuttgart": ("soccer/ger.1", "134"),
    "AC Milan": ("soccer/ita.1", "103"),
    "Roma": ("soccer/ita.1", "104"),
    "Atalanta": ("soccer/ita.1", "105"),
    "Inter Milan": ("soccer/ita.1", "110"),
    "Juventus": ("soccer/ita.1", "111"),
    "Napoli": ("soccer/ita.1", "114"),
    "Monaco": ("soccer/fra.1", "174"),
    "Lyon": ("soccer/fra.1", "167"),
    "PSG": ("soccer/fra.1", "160"),
    "Benfica": ("soccer/por.1", "1929"),
    "Porto": ("soccer/por.1", "437"),
    "Fenerbahce": ("soccer/tur.1", "436"),
    "Galatasaray": ("soccer/tur.1", "432"),
    "Inter Miami": ("soccer/usa.1", "20232"),
    "Al Nassr": ("soccer/ksa.1", "817"),
}


LEAGUE_TEAMS = ("https://site.api.espn.com/apis/site/v2/sports/{path}/teams")

# Every league one of our 32 clubs plays in. We pull all teams in these, not
# just our 32, because the ratings game and ESPN disagree about squads: a
# player loaned out or sold after FC 27 shipped is still in his old club's
# in-game squad but sits on a different ESPN roster. Indexing whole leagues
# lets the merge find him anyway -- height and weight do not depend on club.
SOCCER_LEAGUES = ["soccer/eng.1", "soccer/esp.1", "soccer/ger.1",
                  "soccer/ita.1", "soccer/fra.1", "soccer/por.1",
                  "soccer/tur.1", "soccer/usa.1", "soccer/ksa.1"]


def _league_team_ids(fetch: Fetcher, path: str) -> list[tuple[str, str]]:
    """Every (team_id, display_name) in an ESPN league."""
    raw = fetch.get(LEAGUE_TEAMS.format(path=path), timeout=45)
    if raw is None:
        return []
    try:
        payload = json.loads(raw)
        teams = payload["sports"][0]["leagues"][0]["teams"]
    except (ValueError, KeyError, IndexError):
        log(f"  ! {path}: could not read team list")
        return []
    return [(t["team"]["id"], t["team"].get("displayName") or t["team"]["id"])
            for t in teams]


def _athletes(payload: dict) -> list[dict]:
    """ESPN returns a flat list for soccer and position groups for NFL/NBA."""
    athletes = payload.get("athletes") or []
    if athletes and isinstance(athletes[0], dict) and "items" in athletes[0]:
        return [a for group in athletes for a in group.get("items", [])]
    return athletes


def _fetch_team(fetch: Fetcher, path: str, team: str) -> list[dict]:
    raw = fetch.get(ROSTER.format(path=path, team=team), timeout=45)
    if raw is None:
        return []
    try:
        payload = json.loads(raw)
    except ValueError:
        log(f"  ! {path}/{team}: response was not JSON")
        return []

    people = []
    for athlete in _athletes(payload):
        people.append({
            "name": athlete.get("displayName"),
            "height_in": parse_height_inches(athlete.get("displayHeight") or ""),
            "weight_lb": to_int(athlete.get("weight")),
            "position": ((athlete.get("position") or {}).get("abbreviation")),
        })
    return people


def run(refresh: bool = False) -> dict:
    fetch = Fetcher("espn", delay=0.5, refresh=refresh)
    out: dict[str, dict] = {"football": {}, "basketball": {}, "soccer": {}}

    for sport, path, teams in [
        ("football", "football/nfl", NFL_TEAMS),
        ("basketball", "basketball/nba", NBA_TEAMS),
    ]:
        for index, (slug, abbr) in enumerate(teams.items(), 1):
            people = _fetch_team(fetch, path, abbr)
            out[sport][slug] = people
            log(f"[espn/{sport}] ({index}/{len(teams)}) {slug}: {len(people)}")

    # Our 32 clubs, keyed by our own club names so the merge can match within
    # the right team.
    ours = {team_id for _, team_id in SOCCER_CLUBS.values()}
    for index, (club, (path, team_id)) in enumerate(SOCCER_CLUBS.items(), 1):
        people = _fetch_team(fetch, path, team_id)
        out["soccer"][club] = people
        log(f"[espn/soccer] ({index}/{len(SOCCER_CLUBS)}) {club}: {len(people)}")

    # Then every other club in those leagues, keyed by ESPN's own name, purely
    # to widen the league-wide fallback pool for loanees and departures.
    for path in SOCCER_LEAGUES:
        teams = _league_team_ids(fetch, path)
        extra = [(tid, name) for tid, name in teams if tid not in ours]
        for index, (team_id, name) in enumerate(extra, 1):
            people = _fetch_team(fetch, path, team_id)
            out["soccer"][f"{path}:{name}"] = people
        log(f"[espn/soccer] {path}: +{len(extra)} other clubs for fallback")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / "espn_bio.json"
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    total = sum(len(v) for sport in out.values() for v in sport.values())
    log(f"wrote data/espn_bio.json ({total} athletes)")
    return out


if __name__ == "__main__":
    run()
