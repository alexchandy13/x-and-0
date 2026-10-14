"""NFL ratings from maddenratings.com (one page per player)."""

from .card_site import SiteConfig, scrape_and_write

# The site's teams-sitemap.xml also lists nfl-free-agency and nfl-prospects,
# which are not real teams; this is the 32-team league only.
NFL_TEAMS = [
    "arizona-cardinals", "atlanta-falcons", "baltimore-ravens", "buffalo-bills",
    "carolina-panthers", "chicago-bears", "cincinnati-bengals", "cleveland-browns",
    "dallas-cowboys", "denver-broncos", "detroit-lions", "green-bay-packers",
    "houston-texans", "indianapolis-colts", "jacksonville-jaguars",
    "kansas-city-chiefs", "las-vegas-raiders", "los-angeles-chargers",
    "los-angeles-rams", "miami-dolphins", "minnesota-vikings",
    "new-england-patriots", "new-orleans-saints", "new-york-giants",
    "new-york-jets", "philadelphia-eagles", "pittsburgh-steelers",
    "san-francisco-49ers", "seattle-seahawks", "tampa-bay-buccaneers",
    "tennessee-titans", "washington-commanders",
]

CONFIG = SiteConfig(
    name="football",
    domain="maddenratings.com",
    league="NFL",
    teams=NFL_TEAMS,
)


def run(refresh: bool = False, team_limit: int | None = None) -> dict:
    return scrape_and_write(CONFIG, refresh=refresh, team_limit=team_limit)
