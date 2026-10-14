"""Shared crawler for maddenratings.com and theshowratings.com.

Both sites run the same WordPress theme, so the shape is identical:

  /teams-sitemap.xml   -> every team page URL
  /teams/<team-slug>   -> roster table, one row per player, linking to…
  /<player-slug>       -> the player's full attribute breakdown

Attributes only exist on the player pages, so this is one request per player.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .common import (Fetcher, log, parse_card_attributes, soup, text, to_int,
                     write_dataset)


@dataclass
class SiteConfig:
    name: str            # dataset name, e.g. "football"
    domain: str          # e.g. "maddenratings.com"
    league: str
    teams: list[str]     # team slugs to crawl, in canonical order
    delay: float = 1.0


def _position_from_subtext(subtext: str) -> str | None:
    """Recover a position when the roster cell has no /lists/<position> link.

    Designated hitters on theshowratings are rendered as plain text rather than
    a link, so the usual lookup misses them. The two sites order the subtext
    differently:

        theshowratings:  '#21 Diamondbacks | Designated Hitter | L/R'
        maddenratings:   '#52 C | Oklahoma'   /  '#9 HB | Power Back'

    so check Madden's leading slot (a short uppercase code) before falling
    back to The Show's second field.
    """
    if not subtext:
        return None
    parts = [part.strip() for part in subtext.split("|")]
    head = re.sub(r"^#\d+\s*", "", parts[0]).strip()
    if re.fullmatch(r"[A-Z0-9]{1,5}", head):
        return head
    if len(parts) >= 2 and re.fullmatch(r"[A-Za-z][A-Za-z\- ]{1,24}", parts[1]):
        return parts[1]
    return None


def _roster(page, domain: str) -> list[dict]:
    """Pull one entry per player out of a team page's roster table."""
    table = page.find("table")
    if table is None:
        return []

    players = []
    player_href = re.compile(re.escape(domain) + r"/([a-z0-9][a-z0-9-]*)$")
    for row in table.find_all("tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) < 3:
            continue  # header row

        link = None
        for anchor in cells[1].find_all("a", href=True):
            match = player_href.search(anchor["href"])
            if match:
                link = match.group(1)
                break
        if link is None:
            continue

        # Position is always rendered as a link into /lists/<position>.
        position = None
        for anchor in cells[1].find_all("a", href=re.compile(r"/lists/")):
            position = text(anchor) or None
            if position:
                break

        subtext = text(cells[1].find("span", class_=re.compile("subtext")))
        if position is None:
            position = _position_from_subtext(subtext)
        jersey = re.search(r"#(\d+)", subtext)

        players.append({
            "slug": link,
            "name": text(cells[1].find("span", class_="entry-font")) or None,
            "position": position,
            "jersey": to_int(jersey.group(1)) if jersey else None,
            "overall": to_int(cells[2].get("data-sort") or text(cells[2])),
            "subtext": subtext or None,
        })

    return players


def _player(page) -> dict:
    """Parse a player page into attributes plus whatever bio we can see."""
    attributes, categories = parse_card_attributes(page)

    edition = None
    heading = page.find("h1")
    for node in page.find_all(["h2", "div"], class_=re.compile("card-header")):
        match = re.search(r"((?:Madden NFL|MLB The Show)\s+\d+)", text(node))
        if match:
            edition = match.group(1)
            break

    total = None
    for node in page.find_all("div", class_="d-flex"):
        label = text(node)
        if label.startswith("Total Attributes"):
            total = to_int(label)
            break

    return {
        "name_on_page": text(heading) or None,
        "edition": edition,
        "total_attributes": total,
        "category_ratings": categories,
        "attributes": {k: v["value"] for k, v in attributes.items()},
        "attribute_meta": {
            k: {"label": v["label"], "abbrev": v["abbrev"], "category": v["category"]}
            for k, v in attributes.items()
        },
    }


def scrape(config: SiteConfig, refresh: bool = False,
           team_limit: int | None = None) -> dict:
    fetch = Fetcher(config.name, delay=config.delay, refresh=refresh)
    base = f"https://www.{config.domain}"

    teams_out: list[dict] = []
    players_out: list[dict] = []
    attribute_meta: dict[str, dict] = {}
    edition = None

    team_slugs = config.teams[:team_limit] if team_limit else config.teams
    for index, team_slug in enumerate(team_slugs, 1):
        html = fetch.get(f"{base}/teams/{team_slug}")
        if html is None:
            log(f"[{config.name}] {team_slug}: team page unavailable, skipping")
            continue

        page = soup(html)
        roster = _roster(page, config.domain)
        team_name = re.sub(r"\s+(Madden NFL|MLB The Show)\s+\d+.*$", "",
                           text(page.find("h1")) or team_slug).strip()
        log(f"[{config.name}] ({index}/{len(team_slugs)}) {team_name}: "
            f"{len(roster)} players")

        teams_out.append({
            "slug": team_slug,
            "name": team_name or team_slug,
            "league": config.league,
            "player_count": len(roster),
        })

        for entry in roster:
            player_html = fetch.get(f"{base}/{entry['slug']}")
            if player_html is None:
                log(f"  ! {entry['slug']}: player page unavailable")
                continue
            detail = _player(soup(player_html))
            edition = edition or detail["edition"]
            attribute_meta.update(detail.pop("attribute_meta"))

            # Some players (usually unrated prospects) have an overall but
            # dashes for every attribute on the source page.
            players_out.append({
                "unrated": not detail["attributes"],
                "slug": entry["slug"],
                "name": entry["name"] or detail["name_on_page"],
                "team": team_name or team_slug,
                "team_slug": team_slug,
                "position": entry["position"],
                "jersey": entry["jersey"],
                "overall": entry["overall"],
                "subtext": entry["subtext"],
                "total_attributes": detail["total_attributes"],
                "category_ratings": detail["category_ratings"],
                "attributes": detail["attributes"],
            })

    return {
        "sport": config.name,
        "league": config.league,
        "source": config.domain,
        "edition": edition,
        "unrated_players": sum(1 for p in players_out if p["unrated"]),
        "attribute_meta": attribute_meta,
        "teams": teams_out,
        "players": players_out,
    }


def scrape_and_write(config: SiteConfig, refresh: bool = False,
                     team_limit: int | None = None) -> dict:
    payload = scrape(config, refresh=refresh, team_limit=team_limit)
    write_dataset(config.name, payload)
    return payload
