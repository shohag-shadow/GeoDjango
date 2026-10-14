from django.contrib.gis.db.models.functions import Distance
from django.core.paginator import Paginator
from django.db import models
from django.db.models import Q
from django.shortcuts import get_object_or_404, render

from location.models import Location
from property.forms import PropertySearchForm
from property.models import Property


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

    if form.is_valid():
        data = form.cleaned_data

        if data.get("q"):
            term = data["q"]
            qs = qs.filter(
                Q(title__icontains=term)
                | Q(description__icontains=term)
                | Q(location__name__icontains=term)
                | Q(location__city__icontains=term)
                | Q(location__country__icontains=term)
            )

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

        if location:
            if location.point:
                qs = (
                    qs
                    .annotate(distance=Distance("point", location.point))
                    .filter(point__distance_lte=(location.point, D(km=radius_km)))
                    .order_by("distance")
                )
            else:
                qs = qs.filter(location=location)

    if not qs.query.order_by:
        qs = qs.order_by("-is_featured", "-created_at")

    paginator = Paginator(qs, 12)
    page = paginator.get_page(request.GET.get("page"))

    return render(request, "property/search.html", {
        "form": form,
        "page": page,
        "total": paginator.count,
    })


# ▼▼▼ ADD / REPLACE THIS FUNCTION ▼▼▼
def detail(request, slug):
    prop = get_object_or_404(
        Property.objects
        .select_related("location")
        .prefetch_related("images"),
        slug=slug,
        is_active=True,
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