"""Audit the built datasets for data currency.

Answers the only question that really matters after a scrape: is this the
current season, or did something quietly serve stale data?

    python -m scrapers.verify

Exits non-zero if any check fails, so it can gate a roster update.
"""

from __future__ import annotations

import datetime as dt
import json

from .common import DATA_DIR, log

# The editions that correspond to current rosters. Bump these when the next
# year's games ship, otherwise a stale scrape will pass silently.
EXPECTED_EDITIONS = {
    "basketball": "NBA 2K27",
    "football": "Madden NFL 27",
    "baseball": "MLB The Show 26",
    "soccer": "FC 27",
}

EXPECTED_TEAMS = {
    "basketball": 30,
    "football": 32,
    "baseball": 30,
    "soccer": 32,
}

# A capture older than this is suspicious for basketball, which reads through
# archive.org and can fall back to an old snapshot.
MAX_CAPTURE_AGE_DAYS = 14


def check(sport: str) -> list[str]:
    path = DATA_DIR / f"{sport}.json"
    if not path.exists():
        return [f"{sport}: {path.name} missing -- not built yet"]

    data = json.loads(path.read_text(encoding="utf-8"))
    problems = []

    expected = EXPECTED_EDITIONS[sport]
    if data.get("edition") != expected:
        problems.append(
            f"{sport}: edition is {data.get('edition')!r}, expected {expected!r}")

    teams, players = data.get("teams", []), data.get("players", [])
    if len(teams) != EXPECTED_TEAMS[sport]:
        problems.append(
            f"{sport}: {len(teams)} teams, expected {EXPECTED_TEAMS[sport]}")

    empty = [t["name"] for t in teams if not t.get("player_count")]
    if empty:
        problems.append(f"{sport}: teams with no players: {', '.join(empty)}")

    no_attrs = [p for p in players if not p.get("attributes")
                and not p.get("unrated")]
    if no_attrs:
        problems.append(
            f"{sport}: {len(no_attrs)} players missing attributes but not "
            f"flagged unrated")

    # Basketball reads through archive.org, so check each capture individually:
    # a fallback to an old snapshot is exactly how stale data would get in.
    now = dt.datetime.now(dt.timezone.utc)
    for team in teams:
        method = team.get("capture_method")
        if method and method != "save_page_now":
            problems.append(
                f"{sport}: {team['name']} came from {method}, not a fresh capture")
        stamp = team.get("captured_at")
        if stamp:
            captured = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            age = (now - captured).days
            if age > MAX_CAPTURE_AGE_DAYS:
                problems.append(
                    f"{sport}: {team['name']} capture is {age} days old")
        if team.get("edition") and team["edition"] != expected:
            problems.append(
                f"{sport}: {team['name']} is {team['edition']}, not {expected}")

    log(f"{sport:11} {str(data.get('edition')):16} "
        f"{len(teams):>3} teams {len(players):>5} players "
        f"{data.get('unrated_players', 0):>4} unrated "
        f"{'OK' if not problems else 'PROBLEMS'}")
    return problems


def main() -> int:
    log(f"{'sport':11} {'edition':16} {'teams':>9} {'players':>11} "
        f"{'unrated':>12}")
    problems = []
    for sport in EXPECTED_EDITIONS:
        problems.extend(check(sport))

    log("")
    if not problems:
        log("all datasets current")
        return 0
    for problem in problems:
        log(f"  FAIL  {problem}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
