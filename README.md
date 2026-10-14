# x-and-0

A draft game for building a lineup in one sport out of athletes from another —
a soccer XI made of baseball players, a basketball five made of NFL players.

This repo currently holds the **ratings data layer**: scrapers that build a
static dataset of every current player in four leagues, with their full
video-game attribute ratings.

## Datasets

`python -m scrapers.cli all` writes one JSON file per sport into `data/`:

| File | League | Source | Game edition | Attributes per player |
|---|---|---|---|---|
| `data/basketball.json` | NBA, 30 teams | 2kratings.com | NBA 2K27 | 37 |
| `data/football.json` | NFL, 32 teams | maddenratings.com | Madden NFL 27 | ~53 in 7 categories |
| `data/baseball.json` | MLB, 30 teams | theshowratings.com | MLB The Show 26 | 20 (30 for pitchers) |
| `data/soccer.json` | 32 clubs | fcratings.com | EA Sports FC 27 | 29 in 6 categories |

Each file has the same top-level shape:

```jsonc
{
  "sport": "soccer",
  "source": "fcratings.com",
  "edition": "FC 27",
  "unrated_players": 0,          // players the source has no attributes for
  "attribute_meta": { "<key>": { "label": …, "abbrev": …, "category": … } },
  "teams":   [ { "slug": …, "name": …, "player_count": … } ],
  "players": [ { "slug": …, "name": …, "team": …, "position": …,
                 "overall": …, "attributes": { "<key>": 0-99 }, … } ]
}
```

Every attribute the source publishes is scraped, so narrowing to the few
attributes a given lineup cares about is a filter over this data, not a
re-scrape.

`data/soccer_clubs.json` is the hand-verified club list (see the Milan note
below) and is an **input** to the soccer scraper, not an output.

## Usage

```bash
pip install -r requirements.txt

python -m scrapers.cli all                  # build all four datasets
python -m scrapers.cli soccer baseball      # just these two
python -m scrapers.cli football --limit 2   # smoke test: first 2 teams
python -m scrapers.cli all --refresh        # roster update: ignore the cache
python -m scrapers.cli basketball --stale-ok  # don't request fresh captures
```

After any build, audit currency:

```bash
python -m scrapers.verify
```

It checks each dataset against the expected game edition and team count, and
for basketball checks every team individually — that it came from a fresh
capture rather than an archive fallback, and how old that capture is. It exits
non-zero on any problem, so it can gate a roster update. Bump
`EXPECTED_EDITIONS` in `scrapers/verify.py` when next year's games ship,
otherwise a stale scrape passes silently.

Fetched pages are cached under `.cache/` (gitignored) and never expire, so an
interrupted run resumes almost instantly and a re-parse costs no requests.
Pass `--refresh` when you actually want new numbers — that is the flag for a
roster update.

Requests are serialised with a delay (1s for the ratings sites, 6s for
archive.org) and retried with backoff. A full build is roughly 4,000 requests
and a bit over an hour; the three per-player sports dominate that.

## How each source is read

**Baseball and football** (`card_site.py`) are the same WordPress theme, so
they share a crawler: `teams-sitemap.xml` → `/teams/<slug>` roster table →
one `/<player-slug>` page per player, where each attribute category is a
`div.card` with a `card-header`. One request per player.

**Soccer** (`soccer.py`) reads a club page for the roster plus the six card
ratings (PAC/SHO/PAS/DRI/DEF/PHY), then one page per player for the 29
sub-attributes, which live in `div.attr-group` blocks.

**Basketball** (`basketball.py`) is the cheap one. 2kratings renders every
attribute into `data-*` attributes on each roster row, so a single team page
yields the whole roster fully populated — 30 requests for the league, no
per-player fetches.

## Which season each edition reflects

The four games are on different release cycles, so "current" is not the same
calendar season across all four:

| Sport | Edition | Released | Season reflected |
|---|---|---|---|
| Basketball | NBA 2K27 | Sept 2026 | 2026-27 |
| Football | Madden NFL 27 | Aug 2026 | 2026-27 |
| Soccer | EA Sports FC 27 | Sept 2026 | 2026-27 |
| Baseball | MLB The Show 26 | Mar 2026 | 2026 |

Each is the newest edition that exists for that sport. The Show is the odd one
out because it ships in spring; MLB The Show 27 will not land until around
March 2027, so The Show 26 is the current MLB data until then. All four sites
also push in-season ratings updates, which is why `--refresh` is worth running
periodically rather than only at a new edition.

## Two source quirks worth knowing

**2kratings is behind a Cloudflare challenge.** It answers scripted requests
with `cf-mitigated: challenge`, so the scraper does not fetch it directly.
Instead it asks archive.org's public Save Page Now to capture the page and
reads the capture; archive.org does the fetching. If a capture fails the
scraper falls back to the newest existing snapshot, which may be from an older
edition of the game — the `edition` field in the output records which edition
the data actually came from, so check it after a roster update.

**fcratings mislabels both Milan clubs.** EA ships AC Milan as "Milano FC" and
Inter as "Lombardia"; fcratings tries to un-alias these and gets both wrong:

| Real club | fcratings page title | Slug to use |
|---|---|---|
| AC Milan | "Inter Milan" | `inter-milan-131681` |
| Inter Milan | "Brescia Calcio" | `brescia-calcio-131682` |

`data/soccer_clubs.json` is therefore mapped by verified roster contents, not
page titles. `soccer.py` asserts that Lautaro Martínez is in the Inter roster
and Rafael Leão in the AC Milan roster, and aborts the run with an explanation
if not — so if fcratings ever fixes its labels, the scrape fails loudly instead
of silently writing two swapped squads.

Also note: for goalkeepers, fcratings reuses the six outfield slots for GK card
stats (PAC=Diving, SHO=Handling, PAS=Kicking, DRI=Reflexes, PHY=Positioning).
Keepers carry `is_goalkeeper: true` and their real `gk_*` attributes alongside,
so don't read a keeper's "Shooting 89" as shooting ability.

## Unrated players

Some players — typically unrated prospects on the fringe of a roster — have an
overall rating but dashes for every attribute at the source. They are kept in
the dataset with `"unrated": true` and counted in `unrated_players`, so the
game can exclude them from attribute-based scoring rather than treating them as
all-zero.
