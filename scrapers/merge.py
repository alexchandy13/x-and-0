"""Merge ESPN height/weight onto the ratings datasets.

Matching is done by name *within a team*, which keeps the candidate pool to
~20-60 people and makes near-matches safe. Names differ between sources in
predictable ways -- accents ('Vitezslav Jaros'), short forms ('Alisson' vs
'Alisson Becker'), suffixes ('Jr.'), and initials -- so the matcher tries
progressively looser rules and records which rule fired.

Run after both the ratings scrapers and scrapers/espn.py:

    python -m scrapers.merge
"""

from __future__ import annotations

import difflib
import json
import re
import unicodedata

from .common import DATA_DIR, log

SUFFIXES = {"jr", "jnr", "junior", "sr", "snr", "ii", "iii", "iv", "v"}
FUZZY_THRESHOLD = 0.88

# Letters that NFKD does not decompose into ASCII, so a plain a-z filter would
# shred them: 'Yildiz' became 'y ld z' and never matched ESPN's 'Yildiz'.
TRANSLITERATE = str.maketrans({
    "\u0131": "i", "\u0130": "i", "\u00f8": "o", "\u00d8": "o",
    "\u0142": "l", "\u0141": "l", "\u0111": "d", "\u0110": "d",
    "\u00df": "ss", "\u00e6": "ae", "\u00c6": "ae", "\u0153": "oe",
    "\u0152": "oe", "\u00fe": "th", "\u00de": "th", "\u00f0": "d",
    "\u00d0": "d", "\u0127": "h", "\u0167": "t", "\u014b": "n",
})


def normalise(name: str) -> str:
    """Accent-stripped, punctuation-free, suffix-free lowercase name."""
    if not name:
        return ""
    folded = name.translate(TRANSLITERATE)
    decomposed = unicodedata.normalize("NFKD", folded)
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))
    cleaned = re.sub(r"[^a-z ]+", " ", ascii_only.lower())
    parts = [p for p in cleaned.split() if p and p not in SUFFIXES]
    return " ".join(parts)


def _index(people: list[dict]) -> dict:
    """Build lookup tables for one team's ESPN roster."""
    full, last, initial = {}, {}, {}
    for person in people:
        key = normalise(person.get("name") or "")
        if not key:
            continue
        parts = key.split()
        full.setdefault(key, person)
        last.setdefault(parts[-1], []).append(person)
        if len(parts) > 1:
            initial.setdefault((parts[0][0], parts[-1]), []).append(person)
    return {"full": full, "last": last, "initial": initial, "all": people}


def match(name: str, index: dict) -> tuple[dict | None, str]:
    """Find one ESPN athlete for `name`. Returns (athlete, rule_used)."""
    key = normalise(name)
    if not key:
        return None, "no-name"
    parts = key.split()

    if key in index["full"]:
        return index["full"][key], "exact"

    # 'Alisson' -> 'Alisson Becker', or a dataset name that adds a surname.
    prefix = [p for k, p in index["full"].items()
              if k.startswith(key + " ") or key.startswith(k + " ")]
    if len(prefix) == 1:
        return prefix[0], "prefix"

    if len(parts) > 1:
        candidates = index["initial"].get((parts[0][0], parts[-1]), [])
        if len(candidates) == 1:
            return candidates[0], "first-initial+surname"

    candidates = index["last"].get(parts[-1], [])
    if len(candidates) == 1:
        return candidates[0], "surname-unique"

    # Shortened first names: 'Vini' for 'Vinicius'. Require a single candidate
    # and at least four characters so 'Jo' cannot swallow a roster.
    if len(parts) == 1 and len(key) >= 4:
        starts = [p for k, p in index["full"].items()
                  if k.split()[0].startswith(key)]
        if len(starts) == 1:
            return starts[0], "name-prefix"

    close = difflib.get_close_matches(key, list(index["full"]), n=1,
                                      cutoff=FUZZY_THRESHOLD)
    if close:
        return index["full"][close[0]], f"fuzzy:{close[0]}"

    return None, "unmatched"


def _league_index(bio_for_sport: dict) -> dict:
    """One index across every team in the sport.

    The ratings games and ESPN disagree about rosters: a player traded or
    loaned after the game shipped sits on a different team in each source
    (Harvey Elliott is a Liverpool player in FC 27 but has left in ESPN's
    data). Height and weight do not depend on team, so falling back to a
    league-wide lookup recovers them safely -- but only on an unambiguous
    name, never a fuzzy one, since the team no longer constrains the pool.
    """
    everyone = [person for people in bio_for_sport.values() for person in people]
    counts: dict[str, int] = {}
    for person in everyone:
        counts[normalise(person.get("name") or "")] = \
            counts.get(normalise(person.get("name") or ""), 0) + 1
    unique = {normalise(p.get("name") or ""): p
              for p in everyone if counts[normalise(p.get("name") or "")] == 1}

    # Single-token names ('Palhinha') against league-wide surnames, kept only
    # where exactly one person in all nine leagues carries that surname.
    surnames: dict[str, list] = {}
    for person in everyone:
        key = normalise(person.get("name") or "")
        if key:
            surnames.setdefault(key.split()[-1], []).append(person)
    return {"full": unique,
            "surname": {k: v[0] for k, v in surnames.items() if len(v) == 1}}


def _team_key(sport: str, player: dict) -> str:
    """espn_bio.json is keyed by team slug for US sports, club name for soccer."""
    return player["team"] if sport == "soccer" else player["team_slug"]


def run() -> dict:
    bio = json.loads((DATA_DIR / "espn_bio.json").read_text(encoding="utf-8"))
    summary = {}

    for sport in ["football", "basketball", "soccer"]:
        path = DATA_DIR / f"{sport}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        indexes = {team: _index(people) for team, people in bio[sport].items()}
        league = _league_index(bio[sport])

        stats = {"matched": 0, "unmatched": 0, "rules": {}, "misses": []}
        for player in data["players"]:
            index = indexes.get(_team_key(sport, player)) or {
                "full": {}, "last": {}, "initial": {}, "all": []}
            athlete, rule = match(player["name"] or "", index)
            if athlete is None:
                # Different team in each source -- try the whole league.
                key = normalise(player["name"] or "")
                athlete = league["full"].get(key)
                if athlete is not None:
                    rule = "league-wide"
                elif len(key.split()) == 1:
                    athlete = league["surname"].get(key)
                    if athlete is not None:
                        rule = "league-wide-surname"
            stats["rules"][rule.split(":")[0]] = \
                stats["rules"].get(rule.split(":")[0], 0) + 1
            if athlete is None:
                stats["unmatched"] += 1
                if len(stats["misses"]) < 12:
                    stats["misses"].append(f"{player['name']} ({player['team']})")
                continue
            stats["matched"] += 1
            # Never overwrite a measurement the ratings source itself gave us.
            if player.get("height_in") is None and athlete["height_in"]:
                player["height_in"] = athlete["height_in"]
            if player.get("weight_lb") is None and athlete["weight_lb"]:
                player["weight_lb"] = athlete["weight_lb"]
            player["espn_name"] = athlete["name"]

        have_h = sum(1 for p in data["players"] if p.get("height_in"))
        have_w = sum(1 for p in data["players"] if p.get("weight_lb"))
        data["bio_coverage"] = {
            "height": have_h, "weight": have_w, "players": len(data["players"])}
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")

        total = len(data["players"])
        log(f"[merge] {sport:11} matched {stats['matched']}/{total} | "
            f"height {have_h}/{total} | weight {have_w}/{total}")
        log(f"            rules: {stats['rules']}")
        if stats["misses"]:
            log(f"            sample misses: {', '.join(stats['misses'][:6])}")
        summary[sport] = stats

    return summary


if __name__ == "__main__":
    run()
