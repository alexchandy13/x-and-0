"""NBA ratings from 2kratings.com, read through the Wayback Machine.

2kratings.com sits behind a Cloudflare managed challenge (it answers scripted
requests with `cf-mitigated: challenge`), so we do not fetch it directly.
Instead we ask archive.org's public Save Page Now to capture the page and read
the capture. archive.org does the fetching; we only read the archive.

This is by far the cheapest of the four sports: 2kratings renders every
attribute into `data-*` attributes on each roster row, so one team page yields
the whole roster fully populated. 30 requests covers the league.
"""

from __future__ import annotations

import re

from .common import Fetcher, log, soup, text, to_int, write_dataset

TEAM_PAGE = "https://www.2kratings.com/teams/{slug}"
SAVE_NOW = "https://web.archive.org/save/" + TEAM_PAGE
AVAILABLE = "https://archive.org/wayback/available?url=www.2kratings.com/teams/{slug}"

NBA_TEAMS = [
    "atlanta-hawks", "boston-celtics", "brooklyn-nets", "charlotte-hornets",
    "chicago-bulls", "cleveland-cavaliers", "dallas-mavericks", "denver-nuggets",
    "detroit-pistons", "golden-state-warriors", "houston-rockets",
    "indiana-pacers", "los-angeles-clippers", "los-angeles-lakers",
    "memphis-grizzlies", "miami-heat", "milwaukee-bucks",
    "minnesota-timberwolves", "new-orleans-pelicans", "new-york-knicks",
    "oklahoma-city-thunder", "orlando-magic", "philadelphia-76ers",
    "phoenix-suns", "portland-trail-blazers", "sacramento-kings",
    "san-antonio-spurs", "toronto-raptors", "utah-jazz", "washington-wizards",
]

# Row-level data-* keys that are not attribute ratings.
NON_ATTRIBUTE_KEYS = {"full", "short", "potential", "order", "sort"}

PLAYER_HREF = re.compile(r"2kratings\.com/([a-z0-9][a-z0-9-]*)$")


CAPTURE_TS = re.compile(r"/web/(\d{14})")


def _capture_timestamp(html: str) -> str | None:
    """The Wayback capture timestamp embedded in a snapshot's rewritten URLs."""
    match = CAPTURE_TS.search(html or "")
    if not match:
        return None
    raw = match.group(1)
    return (f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}T"
            f"{raw[8:10]}:{raw[10:12]}:{raw[12:14]}Z")


def _fetch_team(fetch: Fetcher, slug: str, fresh: bool) -> tuple[str | None, str | None]:
    """Get a team page's HTML out of the Wayback Machine.

    With `fresh`, ask Save Page Now for a new capture (what you want for a
    roster update). Falls back to the most recent existing snapshot, which may
    be from a previous edition of the game -- the returned HTML records which.
    """
    if fresh:
        html = fetch.get(SAVE_NOW.format(slug=slug), timeout=180)
        if html and "data-shot-3pt=" in html:
            return html, "save_page_now"
        log(f"  ! {slug}: Save Page Now did not return a usable capture, "
            f"falling back to newest existing snapshot")

    import json as _json
    meta = fetch.get(AVAILABLE.format(slug=slug), timeout=60)
    if meta:
        try:
            snapshot = _json.loads(meta)["archived_snapshots"].get("closest")
        except (ValueError, KeyError):
            snapshot = None
        if snapshot and snapshot.get("url"):
            # `id_` asks the Wayback Machine for the original unrewritten HTML.
            url = snapshot["url"].replace("/http", "id_/http", 1)
            return fetch.get(url, timeout=120), "existing_snapshot"
    return None, None


def _parse_row(row) -> dict | None:
    cells = row.find_all("td", recursive=False)
    if len(cells) < 3:
        return None

    attributes = {}
    for key, value in row.attrs.items():
        if not key.startswith("data-"):
            continue
        short = key[len("data-"):]
        if short in NON_ATTRIBUTE_KEYS:
            continue
        rating = to_int(value)
        if rating is not None:
            attributes[short.replace("-", "_")] = rating

    name_cell = cells[1]
    link = None
    for anchor in name_cell.find_all("a", href=True):
        match = PLAYER_HREF.search(anchor["href"])
        if match:
            link = match.group(1)
            break

    # 'PG / SG | 6'8" | Crafty Offensive Engine'
    subtext = text(name_cell.find("span", class_=re.compile("subtext")))
    parts = [p.strip() for p in subtext.split("|")] if subtext else []
    positions, height, archetype = [], None, None
    if parts:
        positions = [p.strip() for p in parts[0].split("/") if p.strip()]
    if len(parts) > 1:
        height = parts[1] or None
    if len(parts) > 2:
        archetype = parts[2] or None

    jersey = text(name_cell.find("div", class_="badge-icon"))

    return {
        "slug": link,
        "name": row.get("data-full") or text(name_cell.find("a", class_="player-name")),
        "jersey": to_int(jersey),
        "positions": positions,
        "position": positions[0] if positions else None,
        "height": height,
        "archetype": archetype,
        "overall": to_int(cells[2].get("data-sort") or text(cells[2])),
        "potential": row.get("data-potential"),
        "attributes": attributes,
    }


def run(refresh: bool = False, team_limit: int | None = None,
        fresh: bool = True) -> dict:
    # archive.org is a shared free service; keep well clear of its rate limits.
    fetch = Fetcher("basketball", delay=6.0, refresh=refresh, retries=2)

    teams_out, players_out = [], []
    edition = None
    attribute_keys: set[str] = set()

    slugs = NBA_TEAMS[:team_limit] if team_limit else NBA_TEAMS
    for index, slug in enumerate(slugs, 1):
        html, method = _fetch_team(fetch, slug, fresh=fresh)
        if html is None:
            log(f"[basketball] {slug}: no capture available, skipping")
            continue

        page = soup(html)
        rows = page.find_all("tr", attrs={"data-shot-3pt": True})
        roster = [p for p in (_parse_row(r) for r in rows) if p and p["slug"]]

        found = re.search(r"NBA 2K\d+", html)
        edition = edition or (found.group(0) if found else None)

        name = re.sub(r"\s+NBA 2K\d+.*$", "", text(page.find("h1")) or slug).strip()
        log(f"[basketball] ({index}/{len(slugs)}) {name}: {len(roster)} players")

        teams_out.append({
            "slug": slug,
            "name": name or slug,
            "league": "NBA",
            "player_count": len(roster),
            # Provenance, so data currency is auditable rather than assumed.
            "capture_method": method,
            "captured_at": _capture_timestamp(html),
            "edition": found.group(0) if found else None,
        })
        for player in roster:
            attribute_keys.update(player["attributes"])
            player["unrated"] = not player["attributes"]
            player["team"] = name or slug
            player["team_slug"] = slug
            players_out.append(player)

    payload = {
        "sport": "basketball",
        "league": "NBA",
        "source": "2kratings.com (via web.archive.org)",
        "edition": edition,
        "unrated_players": sum(1 for p in players_out if p["unrated"]),
        "attribute_meta": {
            key: {"label": key.replace("_", " ").title(), "abbrev": None,
                  "category": None}
            for key in sorted(attribute_keys)
        },
        "teams": teams_out,
        "players": players_out,
    }
    write_dataset("basketball", payload)
    return payload
