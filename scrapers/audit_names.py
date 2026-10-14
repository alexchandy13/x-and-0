"""Audit scraped player names for structural defects.

Names are load-bearing: they are the join key to ESPN's height/weight data, so
a mangled name silently costs a player his body measurements. This caught
fcratings rendering an initials avatar into the name cell ('TS Tommy Setford')
and is cheap to re-run after any scrape.

Checks, per sport:
  * leading-initials prefix ('TS Tommy Setford', 'ED Enzo dos Santos')
  * stray position codes left in the name
  * empty, very short, or digit-bearing names
  * duplicate names inside one team
  * for football/baseball: team-page name vs the player page's own <h1>

    python -m scrapers.audit_names
"""

from __future__ import annotations

import glob
import json
import re
from pathlib import Path

from .merge import normalise
from .common import DATA_DIR, REPO_ROOT, log, soup, text

POSITION_CODES = {
    "GK", "RB", "LB", "CB", "RWB", "LWB", "CDM", "CM", "CAM", "RM", "LM",
    "RW", "LW", "CF", "ST", "QB", "HB", "FB", "WR", "TE", "LT", "RT", "LG",
    "RG", "DT", "MIKE", "WILL", "SAM", "LEDG", "REDG", "SS", "FS", "CB", "K",
    "P", "LS", "PG", "SG", "SF", "PF", "C",
}


def initials_prefix(name: str) -> bool:
    """'TS Tommy Setford': a short caps token matching the following initials."""
    match = re.match(r"^([A-Z]{2,3})\s+(.+)$", name or "")
    if not match:
        return False
    token, rest = match.group(1), match.group(2)
    initials = "".join(word[0] for word in rest.split() if word)[:len(token)]
    return token == initials.upper()


def check_dataset(sport: str) -> list[str]:
    data = json.loads((DATA_DIR / f"{sport}.json").read_text(encoding="utf-8"))
    problems = []
    seen: dict[tuple[str, str], int] = {}

    for player in data["players"]:
        name = player.get("name") or ""
        if not name.strip():
            problems.append(f"{sport}: empty name for slug {player['slug']}")
            continue
        if initials_prefix(name):
            problems.append(f"{sport}: initials prefix in {name!r} "
                            f"({player['slug']})")
        if re.search(r"\d", name):
            problems.append(f"{sport}: digits in {name!r} ({player['slug']})")
        if len(name.strip()) < 3:
            problems.append(f"{sport}: suspiciously short name {name!r} "
                            f"({player['slug']})")
        tokens = name.split()
        # Only look past the first token: real first names collide with
        # position codes ('LT Overton' is a defensive lineman actually called
        # LT). A code leading the name is caught by initials_prefix instead.
        stray = [t for t in tokens[1:] if t in POSITION_CODES]
        if stray:
            problems.append(f"{sport}: position code {stray} left in {name!r}")
        key = (player.get("team") or "", name)
        seen[key] = seen.get(key, 0) + 1

    for (team, name), count in seen.items():
        if count > 1:
            problems.append(f"{sport}: {count}x {name!r} within {team}")

    return problems


def check_against_player_pages(sport: str, domain: str) -> list[str]:
    """Cross-check the roster-table name against the player page's own <h1>."""
    data = json.loads((DATA_DIR / f"{sport}.json").read_text(encoding="utf-8"))
    cache = REPO_ROOT / ".cache" / sport
    problems, checked = [], 0

    for player in data["players"]:
        matches = glob.glob(str(cache / f"*{domain}-{player['slug']}-*.html"))
        if not matches:
            continue
        page = soup(Path(matches[0]).read_text(encoding="utf-8",
                                               errors="replace"))
        heading = text(page.find("h1"))
        if not heading:
            continue
        checked += 1
        # The two places disagree cosmetically all the time: the roster table
        # carries accents and curly apostrophes ('Jose Ramirez' vs 'Jose
        # Ramirez', 'Ke'Bryan' vs 'Ke'Bryan') while the <h1> is plain. We keep
        # the richer roster spelling, so only flag differences that survive
        # normalisation.
        if normalise(heading) != normalise(player.get("name") or ""):
            problems.append(f"{sport}: {player['slug']} roster name "
                            f"{player['name']!r} vs page <h1> {heading!r}")
    log(f"  cross-checked {checked} {sport} player pages against their <h1>")
    return problems


def main() -> int:
    problems = []
    for sport in ["soccer", "basketball", "baseball", "football"]:
        found = check_dataset(sport)
        log(f"[audit] {sport:11} {len(found)} name problems")
        problems += found

    for sport, domain in [("baseball", "www-theshowratings-com"),
                          ("football", "www-maddenratings-com")]:
        problems += check_against_player_pages(sport, domain)

    log("")
    if not problems:
        log("all names clean")
        return 0
    for problem in problems[:40]:
        log(f"  FAIL  {problem}")
    log(f"({len(problems)} problems)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
