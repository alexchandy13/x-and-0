"""Club-football ratings from fcratings.com (one page per player).

sofifa.com is behind a Cloudflare managed challenge, so this uses fcratings,
which publishes the same EA Sports FC dataset with the full sub-attribute
breakdown.

Two caveats this module handles:

1. fcratings tries to un-alias EA's placeholder club names and gets both Milan
   clubs wrong. EA ships AC Milan as 'Milano FC' and Inter as 'Lombardia';
   fcratings renders these as 'Inter Milan' and 'Brescia Calcio'. The slug
   mapping in data/soccer_clubs.json is therefore keyed on verified roster
   contents, not page titles, and SANITY_PLAYERS below fails the run loudly if
   the site ever fixes its labels and invalidates the mapping.

2. For goalkeepers, fcratings reuses the six outfield category slots for the GK
   card stats (PAC=Diving, SHO=Handling, PAS=Kicking, DRI=Reflexes,
   PHY=Positioning). Keepers are flagged with `is_goalkeeper` so you do not
   read a keeper's 'Shooting 89' as shooting ability.
"""

from __future__ import annotations

import json
import re

from .common import (DATA_DIR, Fetcher, key_for, log, soup, text, to_int,
                     write_dataset)

BASE = "https://www.fcratings.com"

# A player who must appear in that club's roster. Guards the Milan swap and any
# future re-slugging on the source site.
SANITY_PLAYERS = {
    "Inter Milan": "lautaro-martinez",
    "AC Milan": "rafael-leao",
}

GK_SLOT_MEANING = {
    "pace": "GK Diving",
    "shooting": "GK Handling",
    "passing": "GK Kicking",
    "dribbling": "GK Reflexes",
    "physicality": "GK Positioning",
}

PLAYER_HREF = re.compile(r"fcratings\.com/([a-z][a-z0-9-]*-\d+)$")
SKIP_PATHS = ("clubs", "leagues", "nations", "positions", "lists", "abilities")


def load_clubs() -> list[dict]:
    path = DATA_DIR / "soccer_clubs.json"
    return json.loads(path.read_text(encoding="utf-8"))["clubs"]


def _roster(page) -> list[dict]:
    """Players from a club page's table, with OVR and the six category ratings."""
    table = page.find("table")
    if table is None:
        return []

    headers = [text(th).upper() for th in table.find_all("th")]
    players = []
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 3:
            continue

        slug = None
        for anchor in cells[1].find_all("a", href=True):
            match = PLAYER_HREF.search(anchor["href"])
            if match and not any(f"/{p}/" in anchor["href"] for p in SKIP_PATHS):
                slug = match.group(1)
                break
        if slug is None:
            continue

        # Cells read 'OVR 87', 'PAC 80', … so strip the repeated label.
        values = {}
        for header, cell in zip(headers[2:], cells[2:]):
            values[header.lower()] = to_int(text(cell))

        name = text(cells[1])
        positions = re.findall(r"\b(GK|RB|LB|CB|RWB|LWB|CDM|CM|CAM|RM|LM|RW|LW|CF|ST)\b",
                               name)
        for position in positions:
            name = re.sub(r"\s*\b" + position + r"\b", "", name)

        players.append({
            "slug": slug,
            "name": name.strip() or None,
            "positions": positions,
            "position": positions[0] if positions else None,
            "overall": values.pop("ovr", None),
            "card_ratings": values,
        })

    return players


def _player(page) -> dict:
    """Parse the attr-group blocks on a player page."""
    categories, attributes = {}, {}
    for group in page.find_all("div", class_="attr-group"):
        heading = group.find("h5")
        rating = group.find("span", class_=re.compile("attr-rating"))
        if not heading:
            continue
        category = text(heading)
        categories[key_for(category)] = to_int(text(rating))

        for attr_row in group.find_all("div", class_="attr-row"):
            spans = attr_row.find_all("span")
            if len(spans) < 2:
                continue
            label, value = text(spans[0]), to_int(text(spans[1]))
            if not label or value is None:
                continue
            # 'Dribbling' exists as both a category and one of its own
            # sub-attributes; namespace the sub-attribute to keep them apart.
            attr_key = key_for(label)
            if attr_key in categories:
                attr_key = f"{key_for(category)}_{attr_key}"
            attributes[attr_key] = {"label": label, "category": category,
                                    "value": value}

    overall = None
    for span in page.find_all(["span", "div", "h2"]):
        if text(span).strip() == "OVR":
            sibling = span.find_next(string=re.compile(r"^\s*\d{1,3}\s*$"))
            overall = to_int(sibling)
            break

    return {"overall": overall, "categories": categories, "attributes": attributes}


def run(refresh: bool = False, club_limit: int | None = None) -> dict:
    fetch = Fetcher("soccer", delay=1.0, refresh=refresh)

    clubs = load_clubs()
    clubs = clubs[:club_limit] if club_limit else clubs

    teams_out, players_out = [], []
    attribute_meta: dict[str, dict] = {}
    edition = None

    for index, club in enumerate(clubs, 1):
        html = fetch.get(f"{BASE}/clubs/{club['slug']}")
        if html is None:
            log(f"[soccer] {club['name']}: club page unavailable, skipping")
            continue

        page = soup(html)
        roster = _roster(page)
        found = re.search(r"FC\s*\d{2}", text(page.find("title")) or "")
        edition = edition or (found.group(0) if found else None)

        expected = SANITY_PLAYERS.get(club["name"])
        if expected and not any(p["slug"].startswith(expected) for p in roster):
            raise SystemExit(
                f"[soccer] sanity check failed: {club['name']} "
                f"(slug {club['slug']}) does not contain '{expected}'. "
                f"fcratings may have corrected its mislabelled Milan clubs -- "
                f"re-verify the slugs in data/soccer_clubs.json before re-running."
            )

        log(f"[soccer] ({index}/{len(clubs)}) {club['name']}: {len(roster)} players")
        teams_out.append({
            "slug": club["slug"],
            "name": club["name"],
            "page_title": club.get("page_title"),
            "league": "club",
            "player_count": len(roster),
        })

        for entry in roster:
            player_html = fetch.get(f"{BASE}/{entry['slug']}")
            if player_html is None:
                log(f"  ! {entry['slug']}: player page unavailable")
                continue
            detail = _player(soup(player_html))
            is_gk = entry["position"] == "GK" or "goalkeeping" in detail["categories"]

            for key, meta in detail["attributes"].items():
                attribute_meta.setdefault(key, {"label": meta["label"],
                                                "abbrev": None,
                                                "category": meta["category"]})

            players_out.append({
                "unrated": not detail["attributes"],
                "slug": entry["slug"],
                "name": entry["name"],
                "team": club["name"],
                "team_slug": club["slug"],
                "position": entry["position"],
                "positions": entry["positions"],
                "is_goalkeeper": is_gk,
                "overall": entry["overall"] or detail["overall"],
                "card_ratings": entry["card_ratings"],
                "category_ratings": detail["categories"],
                "attributes": {k: v["value"] for k, v in detail["attributes"].items()},
            })

    payload = {
        "sport": "soccer",
        "league": "club",
        "source": "fcratings.com",
        "edition": edition,
        "unrated_players": sum(1 for p in players_out if p["unrated"]),
        "notes": {
            "milan_mislabel": "fcratings swaps the two Milan clubs; slugs in "
                              "data/soccer_clubs.json are mapped by roster.",
            "goalkeeper_slots": GK_SLOT_MEANING,
        },
        "attribute_meta": attribute_meta,
        "teams": teams_out,
        "players": players_out,
    }
    write_dataset("soccer", payload)
    return payload
