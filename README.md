# Vacation Rentals (GeoDjango)

A small vacation-rental catalogue built with Django, PostgreSQL (PostGIS) and
pgvector. It imports Airbnb-style listing exports, stores their geography and
embeddings, and lets guests browse and search stays through **one merged
search bar** that understands places (*Albany*), semantics
(*bbq 2 room king bed*) and distance (nearby stays, metres/kilometres).

## Features

- **Merged search bar** — one input for destinations, listing names, stay
  types and free text, with a text-only autocomplete dropdown.
- **Semantic search** — listings and places are embedded with
  `all-MiniLM-L6-v2` and ranked by cosine similarity (pgvector + HNSW).
- **Spatial search** — radius (`DWithin`) and boundary (`Intersects`) queries
  via PostGIS / GeoDjango.
- **Similarity & distance** — the detail page lists nearby stays and how far
  each one is (shown in metres under 1 km, otherwise kilometres).
- **Multi-format import** — load listings from CSV, TSV, Parquet or Excel,
  from the command line or straight from the Django admin; re-imports are
  idempotent (update, not duplicate).
- **Admin data management** — upload/replace a listings file and rebuild
  embeddings from the admin UI.
- **CPU-only by default** — no GPU / CUDA downloads.

## Tech stack

| Layer | Choice |
| --- | --- |
| Web | Django 5.x |
| API | Django REST Framework |
| Database | PostgreSQL 17 + PostGIS + pgvector (Docker) |
| Search | pgvector cosine similarity (HNSW), PostGIS geography |
| Embeddings | `sentence-transformers` (`all-MiniLM-L6-v2`, CPU) |
| Data import | `pandas`, `pyarrow`, `openpyxl` |

## Prerequisites

- Python 3.12+
- Docker with Docker Compose (`docker compose` or legacy `docker-compose`)
- ~2 GB free disk for the embedding model and the database

## Quick start

```bash
./run.sh
```

`run.sh` is a one-shot, re-runnable setup script. It will:

1. check prerequisites (`python3`, Docker, Docker Compose);
2. create a virtualenv at `.venv/` and install `requirements.txt`;
3. create `.env` from `.env.example` (first run only);
4. start PostgreSQL (PostGIS + pgvector) with Docker Compose and wait until it
   accepts connections;
5. apply migrations;
6. create the admin superuser from `.env` (first run only);
7. seed the database from `data/listings.csv` (first run only — skipped when
   properties already exist), building embeddings;
8. start the development server.

Step 7 is slower on the first run: the `all-MiniLM-L6-v2` model is downloaded
once and then embeddings are built for all 500 sample listings.

Once it's up:

| URL | What |
| --- | --- |
| <http://127.0.0.1:8000/> | Home — search bar + featured stays |
| <http://127.0.0.1:8000/admin/> | Admin |


The default admin account is created from `.env` — `admin` / `password123`
(email `your_email@gmail.com`). Change `DJANGO_SUPERUSER_*` in `.env` before
the first run if you want different credentials, or edit the user later in the
admin.

### run.sh options

```bash
./run.sh --no-install    # skip pip install
./run.sh --no-seed       # skip the data import
./run.sh --no-server     # set everything up, don't start the server
./run.sh -h              # help
```

Expose the dev server to other machines (needed for some remote previews):

```bash
SERVER_HOST=0.0.0.0 ./run.sh
SERVER_PORT=8080 ./run.sh
```

The seed import can also be re-pointed with the `SEED_FILE`, `SEED_COUNTRY`,
`SEED_STATE` and `SEED_CITY` environment variables (defaults:
`data/listings.csv`, United States / New York / Albany).

## Manual setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then edit if needed
docker compose up -d --build  # PostgreSQL + PostGIS + pgvector
python manage.py migrate
python manage.py import_properties data/listings.csv \
    --country "United States" --state "New York" --city "Albany"
python manage.py runserver
```

## Configuration

Settings are read from `.env` (via `django-environ`):

| Variable | Default | Purpose |
| --- | --- | --- |
| `DB_NAME` | `geodjango` | Database name |
| `DB_USER` | `postgres` | Database user |
| `DB_PASSWORD` | `postgres` | Database password |
| `DB_HOST` | `127.0.0.1` | Database host |
| `DB_PORT` | `5442` | Host port mapped to the container |
| `DJANGO_SUPERUSER_USERNAME` | `admin` | Admin username created on first setup |
| `DJANGO_SUPERUSER_PASSWORD` | `password123` | Admin password created on first setup |
| `DJANGO_SUPERUSER_EMAIL` | `your_email@gmail.com` | Admin email created on first setup |

## Importing listings

### Command line

```bash
python manage.py import_properties data/listings.csv \
  --country "United States" --state "New York" --city "Albany"
```

CSV, TSV, Parquet and Excel files are auto-detected by extension. The importer
creates/updates the `Location`, normalises each row, derives the location's
geometry (centroid + convex-hull boundary) from the listings, and builds the
embeddings.

| Flag | Effect |
| --- | --- |
| `--country` / `--state` / `--city` | Where the file belongs (`--country` and `--city` required) |
| `--dry-run` | Parse and validate everything, then roll back — nothing is written |
| `--no-embed` | Import rows and geometry without building embeddings |
| `--reembed` | Clear and rebuild embeddings for the location |

Re-importing the same file updates existing listings instead of duplicating
them; re-imports only embed listings that don't have an embedding yet unless
`--reembed` is passed.

### From the admin

Open *Properties*, choose the action **"Import / update listings from a file"**,
upload a file, fill in country/state/city and optionally tick *Rebuild
embeddings*. The uploaded file is saved under `data/` so it can be re-imported
from the command line too.

## Search

Everything is driven by one search bar whose text is interpreted in this
order:

1. a **stay type** inside the text (`"Albany, Entire home"` → filter
   `Entire home`);
2. a **place** (exact, partial, token or semantic match → spatial radius
   around it);
3. whatever text remains is ranked **semantically** against the listing
   embeddings.

Examples:

| Query | Result |
| --- | --- |
| `Albany` | Stays within ~25 km of Albany, nearest first |
| `bbq 2 room king bed` | Best semantic matches (amenities, room layout, description) |
| `Albany bbq 2 bedroom` | Filtered to Albany **and** ranked by "bbq 2 bedroom" |
| `private room` | Stay-type filter matching every "Private room in ..." variant |

The behind-the-scenes pipeline (thresholds, strategy cascades, autocomplete
rules) is documented in [explanation.md](explanation.md#5-the-search-engine).

## API

| Method & path | Description |
| --- | --- |
| `GET /api/properties/` | Search (params: `q`, `location`, `property_type`, `min_price`, `max_price`, `bedrooms`, `page`, `page_size`) |
| `GET /api/properties/<slug>/` | Listing detail + `nearby` |
| `GET /api/properties/<slug>/nearby/` | Nearby stays (`radius_km`, `limit`) |
| `GET /api/locations/` | Active places with `property_count` (`country` filter) |
| `GET /api/locations/autocomplete/` | Search-bar suggestions (`q`, `limit`) |
| `GET /api/health/` | Database / PostGIS / pgvector status |

Example:

```bash
curl "http://127.0.0.1:8000/api/properties/?q=bbq%202%20room%20king%20bed"
```

Search responses include `count`, `page`, `num_pages` and metadata explaining
what the engine did (`semantic`, `spatial`, `resolved_location`,
`property_type`).

## Testing

```bash
python manage.py test
```

The test suite covers form/query validation, the import (CSV/Parquet/Excel and
the admin upload flow), spatial helpers, semantic search, the merged search
bar, the suggestion serializer, the HNSW indexes, and the distance math
(asserted against a haversine on the stored coordinates). Embedding calls are
patched with deterministic vectors, so the suite runs offline without
downloading the model.

## Project structure

```
GeoDjango/            project settings & root URLs
location/             places: model, admin, migration (pgvector extension)
property/             listings: models, search services, DRF API, views,
                      forms, admin, import command, templates, static
property_image/       listing images (admin inline)
embeddings.py         embedding model + document builders
data/listings.csv     sample dataset (500 Albany, NY listings)
docker/               Postgres init (PostGIS + pgvector extensions)
Dockerfile            Postgres 17 + PostGIS + pgvector image
docker-compose.yml    database service (host port 5442)
run.sh                one-shot setup + launch
explanation.md        how the project works, in depth
```

## Troubleshooting

- **`run.sh` stops at the prerequisite check** — make sure the Docker daemon
  is running and `docker compose version` (or `docker-compose --version`)
  succeeds.
- **Port 5442 already in use** — change the host port in `docker-compose.yml`
  (`"5442:5432"`) and set `DB_PORT` in `.env` to match.
- **First run seems stuck importing** — the model download + embedding of 500
  listings can take a few minutes on first run. Progress is printed per batch.
- **Search returns nothing semantically after editing data** — listings are
  embedded at import time only. Re-import with `--reembed`, or tick *Rebuild
  embeddings* in the admin upload page.
- **Start over from scratch** — `docker compose down -v` removes the database
  volume; the next `./run.sh` recreates, migrates and seeds everything.
