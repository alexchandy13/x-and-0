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

After any build, audit currency and names:

```bash
python -m scrapers.verify        # editions, team counts, capture freshness
python -m scrapers.audit_names   # name defects across all four sports
```

`audit_names` exists because names are the join key to ESPN's height/weight
data, so a mangled name silently costs a player his measurements. It checks for
initials prefixes, stray position codes, empty or digit-bearing names and
intra-team duplicates, and for football/baseball cross-checks the roster-table
name against the player page's own `<h1>`. It found fcratings rendering an
initials avatar into six names ('TS Tommy Setford').

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


## Scoring engine (`engine/`)

`engine/model.py` loads the two CSV inputs; `engine/scoring.py` scores any
player at any lineup position.

```python
from engine.scoring import Scorer

scorer = Scorer()
scorer.score(player, "Starting Pitcher")        # one player, one position
scorer.candidates("Goalkeeper", "basketball")   # every NBA player, best first
```

**Inputs.** `weights/` gives each of the 49 lineup positions a 1-10 weight for
the ten athleticism attributes, plus Height/Weight thresholds and a Hand/Foot
requirement. `attributes/` gives, per *source* sport, which in-game attributes
feed each athleticism attribute. Source sport, not target: when an NBA player
fills a soccer position, his NBA ratings are read through the NBA split and
scored against the soccer position's weights.

**Method.**

1. Every in-game attribute becomes a **percentile within its own pool and
   attribute**. Pools are the groups sharing a split: NBA, NFL, soccer
   outfield, MLB pitchers, MLB position players. Percentiling per attribute
   (not per sport) is what makes the four games comparable -- The Show's
   individual attributes run lower on average than Madden's even though both
   top out near 99.
2. Each athleticism attribute is the **mean** of its inputs' percentiles.
3. Position score is the **weighted mean** of those ten, by the position's
   weights.
4. Penalties multiply, subtractive and floored at zero: 10% per inch under the
   height ideal, 10% per 10lb under the weight ideal, 15% for a wrong-footed
   winger or fullback. A wrong-handed shortstop, second or third baseman is
   ineligible outright.

Height ideals are 6'1"/6'3"/6'5"/6'7" for weights 2-5; weight ideals are
200/225/250/275lb. A weight of 1 means the position does not care.

**Decisions the data forced, all adjustable at the top of `scoring.py`:**

- *Missing athleticism attributes.* Pitchers have no Processing inputs and
  position players no Stamina inputs. Rather than scoring those zero, the
  attribute is dropped and the position's remaining weights renormalised --
  otherwise every pitcher would eat a silent penalty wherever Processing is
  valued.
- *Pitcher lift.* Non-pitching ratings are multiplied by 1.25 (capped at 99)
  before percentiles, identified by the source's own "Pitching" category
  rather than a hand-maintained list.
- *Soccer goalkeepers* (`INCLUDE_SOCCER_GOALKEEPERS = False`). There is no GK
  attribute split, and keepers' outfield ratings are near-zero by construction
  (Alisson has Finishing 13), so scoring them on the outfield split would rank
  all 110 last and including them in the outfield percentile pool would
  inflate everyone else. They are excluded as source players until a GK split
  exists; their `gk_*` attributes are already scraped.
- *Unknown hand/foot.* Only The Show and fcratings publish it, so a drafted NBA
  or NFL player has none. Unknown is treated as acceptable, which means the
  hand/foot rules only ever bind on baseball and soccer source players.
- *Unknown height/weight.* ESPN covers ~90% of the soccer pool; the rest are
  academy and reserve players it does not carry in any top division (mean 67
  OVR, mean age 22 -- only 5 of the 352 best-XI players across the 32 clubs).
  Skipping their penalty would make missing data an advantage, so the sport
  median stands in and the result is flagged `size_imputed`.

  Two things were needed to get there. `scrapers/espn.py` pulls **every** club
  in the nine leagues, not just our 32, because a player loaned out or sold
  after FC 27 shipped is still in his old club's in-game squad but sits on a
  different ESPN roster -- Palhinha is a Bayern player in FC 27 and a Benfica
  one at ESPN. And `normalise()` transliterates the letters NFKD leaves alone
  (`i`, `o`, `l`, `d`, `ss`), without which 'Kenan Yildiz' normalised to
  'y ld z' and matched nothing.
- *Sport-code swap.* The weights CSV tags Shortstop `FB` and Strong Safety
  `BS`; both are corrected on load so the CSV stays the source of truth.

### Known limitation: weak positional differentiation

The model as specified ranks mostly by overall quality rather than positional
fit. Scoring all NBA players at each of the 11 soccer positions returns
Giannis Antetokounmpo first **every time**, and top-10 lists overlap 8.3/10
across position pairs.

This is not attribute redundancy -- mean pairwise correlation between the ten
attributes is only 0.25. It is a level effect: elite players sit at the 85th-99th
percentile on most attributes (Giannis is 85+ on eight of ten), so reweighting
shifts little.

It matters because of how the draft works. Within one drawn team's ~17 players,
the Lakers return Luka Doncic as the best pick at all 11 soccer positions, and
the Celtics return Jayson Tatum at all 11. The draft collapses into "take the
best player on the team you drew," and the position you are filling stops
mattering.

A positional-fit term -- scaling the score by how well a player's *centred*
attribute profile matches the position's *centred* weight vector -- fixes much
of this in prototype (the Lakers go from 1 distinct top pick to 3: Reaves at
Striker, Ziaire Williams at centreback, Luka in midfield). It is deliberately
not implemented: it needs bounding to preserve the 0-100 scale, and how much
positional fit should outweigh raw quality is a game-design decision, not a
data one.
