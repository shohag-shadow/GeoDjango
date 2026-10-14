from django.contrib.gis.db.models.functions import Distance
from django.core.paginator import Paginator
from django.db import models
from django.shortcuts import get_object_or_404, render

from location.models import Location
from property.forms import PropertySearchForm
from property.models import Property
from property.services import nearby_properties, search_properties


def home(request):
    featured = (
        Property.objects
        .active()
        .filter(is_featured=True)
        .select_related("location")
        .prefetch_related("images")[:6]
    )
    if not featured:
        featured = (
            Property.objects
            .active()
            .order_by("-created_at")
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
    meta = {"location": None, "property_type": None, "strategy": None, "semantic": False}

    if not request.GET:
        # No filters at all — just browse everything.
        qs, meta = search_properties()
    elif form.is_valid():
        data = form.cleaned_data
        qs, meta = search_properties(
            q=data.get("q") or "",
            location=data.get("location"),
            property_type=data.get("property_type") or None,
            min_price=data.get("min_price"),
            max_price=data.get("max_price"),
            bedrooms=data.get("bedrooms"),
        )
    else:
        # Invalid filters — surface the errors and show no results.
        qs = Property.objects.none()

    paginator = Paginator(qs, 12)
    page = paginator.get_page(request.GET.get("page"))

    return render(request, "property/search.html", {
        "form": form,
        "page": page,
        "total": paginator.count,
        "resolved_location": meta.get("location"),
        "resolved_property_type": meta.get("property_type"),
        "semantic_used": meta.get("semantic"),
        "spatial_used": meta.get("strategy"),
    })


def detail(request, slug):
    prop = get_object_or_404(
        Property.objects
        .select_related("location")
        .prefetch_related("images"),
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

    nearby = nearby_properties(prop, radius_km=5, limit=4)

    return render(request, "property/detail.html", {
        "property": prop,
        "images": images,
        "distance_km": distance_km,
        "nearby": nearby,
        # Kept for backwards compatibility with older templates.
        "similar": nearby,
    })
