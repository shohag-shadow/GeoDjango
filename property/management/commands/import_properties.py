import ast
import json
import re
from pathlib import Path

import pandas as pd
from django.contrib.gis.geos import Point
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils.html import strip_tags
from django.utils.text import slugify

from embeddings import embed_texts
from location.models import Location
from property.models import Property


def read_dataframe(path: Path) -> pd.DataFrame:
    """Load a CSV, Parquet or Excel file into a DataFrame (auto-detected)."""
    suffix = path.suffix.lower()
    if suffix in (".csv", ".txt", ".tsv"):
        sep = "\t" if suffix == ".tsv" else ","
        return pd.read_csv(path, low_memory=False, sep=sep)
    if suffix in (".parquet", ".pq"):
        return pd.read_parquet(path)
    if suffix in (".xlsx", ".xls", ".xlsm"):
        return pd.read_excel(path)
    raise CommandError(
        f"Unsupported file type '{suffix}'. Use CSV, Parquet or Excel."
    )


def parse_price(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return 0
    s = str(v).strip().replace(",", "").lstrip("$").strip()
    try:
        return float(s) if s else 0
    except ValueError:
        return 0


def clean(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return strip_tags(str(v)).strip()


def int_or_zero(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return 0
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def to_float(v):
    """Robust float cast — returns None for NaN/blank/unparseable."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def parse_amenities(v):
    """
    Normalise the Airbnb `amenities` column into a list[str].

    Handles: already-parsed lists (Parquet), JSON strings, Python-repr strings
    and comma separated plain text.
    """
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return []
    if isinstance(v, (list, tuple, set)):
        return [str(x).strip() for x in v if str(x).strip()]

    text = str(v).strip()
    if not text:
        return []

    for loader in (json.loads, ast.literal_eval):
        try:
            parsed = loader(text)
            if isinstance(parsed, (list, tuple, set)):
                return [str(x).strip() for x in parsed if str(x).strip()]
        except (ValueError, SyntaxError):
            continue

    return [part.strip() for part in text.split(",") if part.strip()]


def extract_point(row):
    """
    Try multiple possible column names, validate the ranges,
    return a Point(lon, lat, srid=4326) or None.
    """
    lat = to_float(
        row.get("latitude")
        if row.get("latitude") is not None else row.get("lat")
    )
    lon = to_float(
        row.get("longitude")
        if row.get("longitude") is not None else row.get("lon")
    )
    if lat is None or lon is None:
        return None
    # Sanity: reject obviously bad values (0,0 is a common placeholder)
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return None
    if lat == 0 and lon == 0:
        return None
    return Point(lon, lat, srid=4326)


def extract_bathrooms(row):
    """
    Try the numeric `bathrooms` column first; fall back to parsing
    `bathrooms_text` (e.g. "1.5 baths", "Half-bath").
    """
    n = int_or_zero(row.get("bathrooms"))
    if n:
        return n

    txt = row.get("bathrooms_text")
    if txt is None or (isinstance(txt, float) and pd.isna(txt)):
        return 0
    txt = str(txt).strip().lower()
    if not txt:
        return 0

    # "half-bath" / "half bath" → 1 (most common interpretation)
    if "half" in txt:
        return 1

    m = re.search(r"(\d+(?:\.\d+)?)", txt)
    if not m:
        return 0
    try:
        return int(float(m.group(1)))
    except ValueError:
        return 0


# ---------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------

class Command(BaseCommand):
    help = (
        "Import vacation-rental listings (CSV, Parquet or Excel) with "
        "MiniLM embeddings."
    )

    def add_arguments(self, parser):
        parser.add_argument("path", help="CSV, Parquet or Excel file to import.")
        parser.add_argument("--country", required=True)
        parser.add_argument("--state", default="")
        parser.add_argument("--city", required=True)
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--no-embed", action="store_true")
        parser.add_argument(
            "--reembed", action="store_true",
            help="Rebuild embeddings for every property of this location.",
        )

    def handle(self, *args, **options):
        path = Path(options["path"])
        if not path.exists():
            raise CommandError(f"File not found: {path}")

        df = read_dataframe(path)

        # Sanity print — helps when a column is missing
        wanted = ["latitude", "longitude", "bathrooms", "bathrooms_text",
                  "bedrooms", "price", "amenities", "picture_url"]
        present = [c for c in wanted if c in df.columns]
        missing = [c for c in wanted if c not in df.columns]
        self.stdout.write(f"Loaded {len(df)} rows from {path.name}")
        self.stdout.write(f"Columns found: {present}")
        if missing:
            self.stdout.write(self.style.WARNING(f"Missing: {missing}"))

        location = self._location(
            options["country"], options["state"], options["city"],
        )
        created = updated = skipped = 0

        with transaction.atomic():
            for idx, row in df.iterrows():
                try:
                    _, new = self._property(row, location)
                    created += int(new)
                    updated += int(not new)
                except Exception as exc:
                    self.stderr.write(self.style.ERROR(f"Row {idx}: {exc}"))
                    skipped += 1

            if options["dry_run"]:
                self.stdout.write(self.style.WARNING("Dry-run — rollback"))
                transaction.set_rollback(True)
                return

        self.stdout.write(self.style.SUCCESS(
            f"Properties +{created} updated {updated} | Skipped {skipped}"
        ))

        # Derive spatial geometry for the Location from its properties so that
        # DWithin / Intersects queries (radius search, distance-to-city,
        # nearby properties) have something to work with.
        self._location_geometry(location)

        if options["no_embed"]:
            return

        if options["reembed"]:
            cleared = Property.objects.filter(location=location).update(embedding=None)
            self.stdout.write(f"--reembed: cleared {cleared} embeddings.")

        self._embed(location)

    # ------------------------------------------------------------------

    def _location(self, country, state, city):
        slug = slugify(f"{country}-{state}-{city}")
        obj, _ = Location.objects.update_or_create(
            slug=slug,
            defaults={
                "name": city,
                "country": country,
                "state": state,
                "city": city,
            },
        )
        return obj

    def _property(self, row, location):
        external_id = str(int(row["id"]))
        title = clean(row.get("name")) or f"Listing {external_id}"
        slug = f"{slugify(title)[:180]}-{external_id}"

        defaults = {
            "location": location,
            "title": title,
            "description": clean(row.get("description")),
            "amenities": parse_amenities(row.get("amenities")),
            "property_type": clean(row.get("property_type")),
            "status": "for_rent",
            "price": parse_price(row.get("price")),
            "bedrooms": int_or_zero(row.get("bedrooms")),
            "bathrooms": extract_bathrooms(row),
            "address": clean(row.get("neighbourhood_cleansed")),
            "image_url": clean(row.get("picture_url"))[:500],
            "is_active": True,
        }

        point = extract_point(row)
        if point is not None:
            defaults["point"] = point

        return Property.objects.update_or_create(slug=slug, defaults=defaults)

    # ------------------------------------------------------------------

    def _location_geometry(self, location):
        """Set the location's representative point (centroid) and boundary (hull)."""
        with connection.cursor() as cur:
            cur.execute(
                """
                SELECT AVG(ST_Y(point::geometry)), AVG(ST_X(point::geometry))
                FROM property_property
                WHERE location_id = %s AND point IS NOT NULL
                """,
                [location.pk],
            )
            lat, lon = cur.fetchone()

            if lat is None or lon is None:
                self.stdout.write(self.style.WARNING(
                    "No property coordinates — location geometry left empty."
                ))
                return

            location.point = Point(lon, lat, srid=4326)
            location.save(update_fields=["point"])

            # Convex hull of every listing → a polygon boundary for Intersects.
            cur.execute(
                """
                UPDATE location_location l
                SET boundary = sub.hull
                FROM (
                    SELECT ST_Multi(ST_ConvexHull(ST_Collect(point::geometry)))
                             ::geometry(MultiPolygon, 4326) AS hull
                    FROM property_property
                    WHERE location_id = %s AND point IS NOT NULL
                    HAVING COUNT(*) >= 3
                ) sub
                WHERE l.id = %s
                """,
                [location.pk, location.pk],
            )

        location.refresh_from_db(fields=["point", "boundary"])
        self.stdout.write(
            f"Location geometry → point=({location.point.y:.4f}, "
            f"{location.point.x:.4f}) boundary={'yes' if location.boundary else 'no'}"
        )

    # ------------------------------------------------------------------

    def _embed(self, location):
        # Keep the location vector focused on the place identity itself — mixing
        # in dozens of neighbourhood names dilutes it and breaks queries like
        # "Albany, New York". Neighbourhoods are carried by the *property*
        # embeddings below instead, which is where they actually help.
        location.embedding = embed_texts(
            [f"{location.name}, {location.state}, {location.country}".strip(", ")]
        )[0]
        location.save(update_fields=["embedding"])

        ids = list(Property.objects.filter(
            location=location, embedding__isnull=True,
        ).values_list("id", flat=True))
        if not ids:
            self.stdout.write("No properties need embeddings.")
            return

        self.stdout.write(f"Embedding {len(ids)} properties...")
        for i in range(0, len(ids), 64):
            batch = list(Property.objects.filter(id__in=ids[i : i + 64]))
            vecs = embed_texts([
                "\n\n".join(x for x in [
                    p.title,
                    f"{p.property_type} in {p.address + ', ' if p.address else ''}"
                    f"{p.location.city}, {p.location.state}, {p.location.country}",
                    " ".join(p.amenities or []),
                    p.description,
                ] if x)
                for p in batch
            ])
            for p, v in zip(batch, vecs):
                p.embedding = v
            Property.objects.bulk_update(batch, ["embedding"])
            self.stdout.write(f"  {min(i + 64, len(ids))}/{len(ids)}")

        self.stdout.write(self.style.SUCCESS("Done."))
