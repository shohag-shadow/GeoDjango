import re

from django.contrib.gis.db.models.functions import Distance
from django.db.models import Count, Q
from pgvector.django import CosineDistance

from embeddings import embed_texts
from location.models import Location
from property.models import Property

# Cosine distance below which a free-text query is treated as a location name.
# Measured on this dataset: true place matches ≈ 0.05–0.25, unrelated text ≥ 0.53.
LOCATION_MATCH_MAX_DISTANCE = 0.45

# Internal working radius for spatial search around a resolved location.
# Not user-configurable — a single, predictable default avoids an ambiguous
# "radius" control in the UI.
DEFAULT_RADIUS_KM = 25

# Autocomplete only suggests a place when it is an actual text match or is
# semantically close. Without this, the nearest city leaks into every keystroke.
AUTOCOMPLETE_LOCATION_MAX_DISTANCE = 0.5

# Fallback vocabulary when the database has no listings yet.
DEFAULT_PROPERTY_TYPES = (
    "Entire rental unit",
    "Entire home",
    "Private room",
    "Shared room",
    "Hotel room",
)


# ---------------------------------------------------------------------
# Semantic (pgvector) lookups
# ---------------------------------------------------------------------

def semantic_locations(query: str, limit: int = 10):
    """Locations ranked by cosine similarity to `query`. Lower distance = closer."""
    query = (query or "").strip()
    if not query:
        return Location.objects.none()

    vector = embed_texts([query])[0]
    return (
        Location.objects
        .filter(is_active=True, embedding__isnull=False)
        .annotate(distance=CosineDistance("embedding", vector))
        .order_by("distance")[:limit]
    )


def suggest_locations(query: str, limit: int = 8):
    """
    Destinations for the search-bar suggestion list.

    Text matches (name/city contains the query) come first, then semantically
    close places within ``AUTOCOMPLETE_LOCATION_MAX_DISTANCE``. A query with no
    relevant place returns nothing, so properties can own the dropdown.
    """
    query = (query or "").strip()
    if not query:
        return Location.objects.none()

    results = []
    seen = set()

    text_matches = (
        Location.objects
        .filter(is_active=True)
        .filter(Q(name__icontains=query) | Q(city__icontains=query))
        .order_by("name")[:limit]
    )
    for loc in text_matches:
        results.append(loc)
        seen.add(loc.pk)

    if len(results) < limit:
        vector = embed_texts([query])[0]
        semantic = (
            Location.objects
            .filter(is_active=True, embedding__isnull=False)
            .annotate(distance=CosineDistance("embedding", vector))
            .order_by("distance")[: limit + len(seen)]
        )
        for loc in semantic:
            if loc.pk in seen:
                continue
            if loc.distance is not None and loc.distance > AUTOCOMPLETE_LOCATION_MAX_DISTANCE:
                break
            results.append(loc)
            seen.add(loc.pk)
            if len(results) >= limit:
                break

    return results


def semantic_properties(query: str, limit: int = 24):
    """Properties ranked by cosine similarity to `query`."""
    query = (query or "").strip()
    if not query:
        return Property.objects.none()

    vector = embed_texts([query])[0]
    return (
        Property.objects
        .active()
        .semantic(vector)
        .select_related("location")
        .prefetch_related("images")[:limit]
    )


def suggest_properties(query: str, limit: int = 6):
    """
    Listings for the search-bar suggestion list, so typing a name shows the
    property itself. Name matches come first (strongest intent), then semantic
    neighbours fill the remaining slots.
    """
    query = (query or "").strip()
    if not query:
        return []

    results = []
    seen = set()

    named = (
        Property.objects
        .active()
        .filter(title__icontains=query)
        .select_related("location")
        .order_by("-is_featured", "-created_at")[:limit]
    )
    for prop in named:
        results.append(prop)
        seen.add(prop.pk)

    if len(results) < limit:
        vector = embed_texts([query])[0]
        semantic = (
            Property.objects
            .active()
            .semantic(vector)
            .select_related("location")
            [: limit + len(seen)]
        )
        for prop in semantic:
            if prop.pk in seen:
                continue
            results.append(prop)
            seen.add(prop.pk)
            if len(results) >= limit:
                break

    return results


# ---------------------------------------------------------------------
# Merged search bar: one text input drives both location and free text
# ---------------------------------------------------------------------

def resolve_location(text: str, threshold: float = LOCATION_MATCH_MAX_DISTANCE):
    """
    Turn free text from the single search bar into a Location, or None.

    1. Exact name/city match (most precise signal).
    2. Partial name/city match.
    3. Exact name/city match on any single token, so a place named among other
       words still resolves ("Albany bbq 2 bedroom").
    4. Semantic (pgvector cosine) match, accepted only below `threshold`.
    """
    text = (text or "").strip()
    if not text:
        return None

    for candidate in (text, text.split(",")[0].strip()):
        if not candidate:
            continue
        exact = (
            Location.objects.filter(is_active=True, name__iexact=candidate).first()
            or Location.objects.filter(is_active=True, city__iexact=candidate).first()
        )
        if exact is not None:
            return exact

        partial = (
            Location.objects.filter(is_active=True, name__icontains=candidate).first()
            or Location.objects.filter(is_active=True, city__icontains=candidate).first()
        )
        if partial is not None:
            return partial

    # A place name can be mixed in among other search words
    # ("Albany bbq 2 bedroom"): match any individual token exactly.
    for token in text.split():
        token = token.strip(",.!?;:-")
        if len(token) < 3:
            continue
        token_match = (
            Location.objects.filter(is_active=True, name__iexact=token).first()
            or Location.objects.filter(is_active=True, city__iexact=token).first()
        )
        if token_match is not None:
            return token_match

    best = list(
        Location.objects
        .filter(is_active=True, embedding__isnull=False)
        .annotate(distance=CosineDistance("embedding", embed_texts([text])[0]))
        .order_by("distance")[:1]
    )
    if best and best[0].distance is not None and best[0].distance <= threshold:
        return best[0]
    return None


# ---------------------------------------------------------------------
# Property-type vocabulary (suggested in the same search bar)
# ---------------------------------------------------------------------

def property_type_vocabulary():
    """Distinct property types present in the data, most common first."""
    rows = (
        Property.objects
        .exclude(property_type="")
        .values("property_type")
        .annotate(n=Count("id"))
        .order_by("-n")
    )
    values = [row["property_type"] for row in rows]
    return values or list(DEFAULT_PROPERTY_TYPES)


def suggest_property_types(prefix: str, limit: int = 5):
    """Property types whose name contains `prefix` — for type-ahead suggestions."""
    prefix = (prefix or "").strip().lower()
    if not prefix:
        return []
    matches = [v for v in property_type_vocabulary() if prefix in v.lower()]
    # Prefix matches first, then shortest (most generic) names.
    matches.sort(key=lambda v: (not v.lower().startswith(prefix), len(v)))
    return matches[:limit]


def match_property_type(text: str, vocabulary=None):
    """
    Find a property type named inside `text` (e.g. "Albany, Entire home").

    1. A complete vocabulary phrase inside the text wins (longest first, so
       "private room in home" beats "private room").
    2. Otherwise, if the whole text is a prefix of a known type ("private
       room", "entire"), treat the typed text itself as the filter phrase —
       an icontains match then covers every variant.
    """
    text_l = (text or "").strip().lower()
    if not text_l:
        return None
    vocab = property_type_vocabulary() if vocabulary is None else vocabulary

    for value in sorted(vocab, key=len, reverse=True):
        if value.lower() in text_l:
            return value

    if len(text_l) >= 4 and any(v.lower().startswith(text_l) for v in vocab):
        return text_l
    return None


def strip_phrase(text: str, phrase: str):
    """Remove `phrase` from `text`, returning the remaining search terms."""
    if not phrase or not text:
        return (text or "").strip()
    cleaned = re.sub(re.escape(phrase), "", text, flags=re.IGNORECASE)
    return cleaned.strip(" ,;:-")


def filter_by_location(qs, location, radius_km: int):
    """
    Apply a location filter to a Property queryset, returning ``(strategy, qs)``.

    Priority:
      1. PostGIS DWithin around the location's representative point.
      2. PostGIS Intersects against the location's boundary.
      3. Plain FK filter when the location has no geometry.
    """
    if location.point is not None:
        return (
            "dwithin",
            qs.within_radius(location.point, radius_km, annotate=True).order_by("distance"),
        )

    if location.boundary is not None:
        return "intersects", qs.intersecting(location.boundary)

    return "fk", qs.filter(location=location)


def search_properties(
    q: str = "",
    location=None,
    radius_km: int = DEFAULT_RADIUS_KM,
    property_type: str = None,
    min_price=None,
    max_price=None,
    bedrooms=None,
):
    """
    Single entry point for property search used by both the HTML view and the API.

    The single search bar value (``q``) may combine several intents in one
    string — a place and/or a property type and/or free text about
    amenities/rooms ("bbq 2 room king bed Albany"). We combine them instead of
    picking just one:
      * a resolved place (autocomplete pick or text match) → spatial filter, and
      * any leftover words → semantic ranking over the property embeddings
        (which cover the name, location, description, amenities and rooms).

    Returns ``(queryset, meta)`` where meta describes what the engine did.
    """
    qs = Property.objects.active().select_related("location").prefetch_related("images")

    # --- traditional filters ---
    if min_price is not None:
        qs = qs.filter(price__gte=min_price)
    if max_price is not None:
        qs = qs.filter(price__lte=max_price)
    if bedrooms is not None:
        # "Minimum bedrooms": inclusive lower bound.
        qs = qs.filter(bedrooms__gte=bedrooms)

    q = (q or "").strip()
    meta = {
        "q": q, "location": None, "property_type": None,
        "strategy": None, "semantic": False,
    }

    # The single bar may carry a property type ("Albany, Entire home"). Pull it
    # out so the rest of the text can still resolve to a location or free text.
    matched_type = match_property_type(q)
    remaining = strip_phrase(q, matched_type) if matched_type else q

    resolved_type = property_type or matched_type
    if resolved_type:
        qs = qs.filter(property_type__icontains=resolved_type)
        meta["property_type"] = resolved_type

    # A location picked from the autocomplete wins; otherwise try to resolve
    # the typed text into a place before falling back to semantic search.
    resolved = location if location is not None else (
        resolve_location(remaining) if remaining else None
    )

    # Whatever is left once the place name is removed is free text that should
    # still influence the ranking ("bbq", "2 room", "king bed", ...).
    free_text = remaining
    if resolved is not None:
        for label in (resolved.name, resolved.city):
            free_text = strip_phrase(free_text, label)

    if resolved is not None:
        strategy, qs = filter_by_location(qs, resolved, radius_km)
        meta.update(location=resolved, strategy=strategy)

    if free_text.strip():
        qs = qs.semantic(embed_texts([free_text])[0])
        meta["semantic"] = True
        return qs, meta

    if resolved is not None:
        return qs, meta

    return qs.order_by("-is_featured", "-created_at"), meta


# ---------------------------------------------------------------------
# Nearby (PostGIS DWithin)
# ---------------------------------------------------------------------

def nearby_properties(prop: Property, radius_km: int = 5, limit: int = 4):
    """
    PostGIS (DWithin) neighbours for a property's detail page.

    Every returned property is annotated with ``distance`` — how far it is
    from the property being viewed — so the detail page can show it. Falls
    back to "same location" listings when the property has no point or when
    nothing is close enough, so the section is never empty.
    """
    qs = Property.objects.active().exclude(pk=prop.pk).select_related("location")

    if prop.point is not None:
        nearby = list(
            qs.within_radius(prop.point, radius_km, annotate=True)
            .order_by("distance")[:limit]
        )
        if nearby:
            return nearby
        # No neighbour within the radius: still rank the fallbacks by real
        # distance from this property rather than dropping the annotation.
        qs = qs.annotate(distance=Distance("point", prop.point))

    fallback = list(
        qs.filter(location=prop.location).order_by("-is_featured", "-created_at")[:limit]
    )
    if fallback:
        return fallback

    # Last resort: newest active listings anywhere.
    return list(qs.order_by("-created_at")[:limit])
