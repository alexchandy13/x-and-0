"""MLB ratings from theshowratings.com (one page per player)."""

from .card_site import SiteConfig, scrape_and_write

MLB_TEAMS = [
    "arizona-diamondbacks", "athletics", "atlanta-braves", "baltimore-orioles",
    "boston-red-sox", "chicago-cubs", "chicago-white-sox", "cincinnati-reds",
    "cleveland-guardians", "colorado-rockies", "detroit-tigers", "houston-astros",
    "kansas-city-royals", "los-angeles-angels", "los-angeles-dodgers",
    "miami-marlins", "milwaukee-brewers", "minnesota-twins", "new-york-mets",
    "new-york-yankees", "philadelphia-phillies", "pittsburgh-pirates",
    "san-diego-padres", "san-francisco-giants", "seattle-mariners",
    "st-louis-cardinals", "tampa-bay-rays", "texas-rangers",
    "toronto-blue-jays", "washington-nationals",
]

CONFIG = SiteConfig(
    name="baseball",
    domain="theshowratings.com",
    league="MLB",
    teams=MLB_TEAMS,
)


def run(refresh: bool = False, team_limit: int | None = None) -> dict:
    return scrape_and_write(CONFIG, refresh=refresh, team_limit=team_limit)
