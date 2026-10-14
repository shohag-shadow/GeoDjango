from django.contrib.gis.db.models.functions import Distance
from django.contrib.gis.measure import D
from django.core.paginator import Paginator
from django.db import models
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from pgvector.django import CosineDistance

from embeddings import embed_texts
from location.models import Location
from property.forms import PropertySearchForm
from property.models import Property
from property.services import semantic_locations


def home(request):
    featured = (
        Property.objects
        .filter(is_active=True, is_featured=True)
        .select_related("location")
        .prefetch_related("images")[:6]
    )
    if not featured:
        featured = (
            Property.objects
            .filter(is_active=True)
            .select_related("location")
            .prefetch_related("images")[:6]
        )

    popular_locations = (
        Location.objects
        .filter(is_active=True)
        .annotate(property_count=models.Count("properties"))
        .order_by("-property_count")[:8]
    )

    return render(request, "property/home.html", {
        "form": PropertySearchForm(),
        "featured": featured,
        "popular_locations": popular_locations,
    })


def search(request):
    form = PropertySearchForm(request.GET or None)
    qs = (
        Property.objects
        .filter(is_active=True)
        .select_related("location")
        .prefetch_related("images")
    )

    semantic_used = False

    if form.is_valid():
        data = form.cleaned_data

        # --- traditional filters ---
        if data.get("property_type"):
            qs = qs.filter(property_type__iexact=data["property_type"])
        if data.get("min_price") is not None:
            qs = qs.filter(price__gte=data["min_price"])
        if data.get("max_price") is not None:
            qs = qs.filter(price__lte=data["max_price"])
        if data.get("bedrooms") is not None:
            qs = qs.filter(bedrooms__gte=data["bedrooms"])
        if data.get("bathrooms") is not None:
            qs = qs.filter(bathrooms__gte=data["bathrooms"])

        location = data.get("location")
        radius_km = int(data.get("radius_km") or 25)

        # --- combined: free text + optional location ---
        if data.get("q"):
            query = data["q"]
            vector = embed_texts([query])[0]
            qs = (
                qs
                .filter(embedding__isnull=False)
                .annotate(semantic_distance=CosineDistance("embedding", vector))
                .order_by("semantic_distance")
            )
            semantic_used = True

            # If the user also chose a location, narrow to it.
            # Radius search is skipped here — semantic already captures
            # "near the beach" etc., and stacking a strict radius on top
            # of an unrelated vector query would over-constrain results.
            if location:
                qs = qs.filter(location=location)

        elif location:
            if location.point:
                qs = (
                    qs
                    .annotate(distance=Distance("point", location.point))
                    .filter(point__distance_lte=(location.point, D(km=radius_km)))
                    .order_by("distance")
                )
            else:
                qs = qs.filter(location=location)

    if not semantic_used and not qs.query.order_by:
        qs = qs.order_by("-is_featured", "-created_at")

    paginator = Paginator(qs, 12)
    page = paginator.get_page(request.GET.get("page"))

    return render(request, "property/search.html", {
        "form": form,
        "page": page,
        "total": paginator.count,
        "semantic_used": semantic_used,
    })


def detail(request, slug):
    prop = get_object_or_404(
        Property.objects.select_related("location").prefetch_related("images"),
        slug=slug, is_active=True,
    )

    images = list(prop.images.all())

    distance_km = None
    if prop.point and prop.location.point:
        d = (
            Property.objects
            .filter(pk=prop.pk)
            .annotate(dist=Distance("point", prop.location.point))
            .values_list("dist", flat=True)
            .first()
        )
        if d is not None:
            distance_km = round(d.km, 2)

    similar = (
        Property.objects
        .filter(location=prop.location, is_active=True)
        .exclude(pk=prop.pk)
        .select_related("location")
        .prefetch_related("images")[:4]
    )

    return render(request, "property/detail.html", {
        "property": prop,
        "images": images,
        "distance_km": distance_km,
        "similar": similar,
    })


# ---------------------------------------------------------------------
# Autocomplete API
# ---------------------------------------------------------------------

def autocomplete_locations(request):
    """
    GET /api/locations/autocomplete/?q=<text>&limit=<n>
    Returns JSON list of locations ranked by semantic similarity.
    """
    query = request.GET.get("q", "").strip()
    try:
        limit = min(int(request.GET.get("limit", 8)), 20)
    except (TypeError, ValueError):
        limit = 8

    if query:
        locs = semantic_locations(query, limit=limit)
    else:
        # Empty query — fall back to alphabetical for the initial dropdown.
        locs = Location.objects.filter(is_active=True).order_by("name")[:limit]

    return JsonResponse({
        "query": query,
        "results": [
            {
                "id": loc.pk,
                "name": loc.name,
                "city": loc.city,
                "state": loc.state,
                "country": loc.country,
                "label": f"{loc.city}, {loc.country}",
                "slug": loc.slug,
                "score": round(loc.distance, 4) if hasattr(loc, "distance") else None,
            }
            for loc in locs
        ],
    })