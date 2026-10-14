"""
Django REST Framework endpoints.

These power both the JSON API and the front-end autocomplete widget that the
templates call via ``fetch()``.
"""
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from location.models import Location
from property.models import Property
from property.serializers import (
    AutocompleteQuerySerializer,
    LocationSerializer,
    NearbyQuerySerializer,
    PropertySearchQuerySerializer,
    PropertySerializer,
)
from property.services import (
    nearby_properties,
    search_properties,
    semantic_locations,
    suggest_property_types,
)


def _serialize_location(loc):
    label = ", ".join(p for p in [loc.city, loc.state, loc.country] if p)
    return {
        "id": loc.pk,
        "type": "location",
        "value": loc.pk,
        "name": loc.name,
        "city": loc.city,
        "state": loc.state,
        "country": loc.country,
        "label": label or loc.name,
        "slug": loc.slug,
        "score": round(loc.distance, 4) if hasattr(loc, "distance") else None,
    }


def _serialize_property_type(value):
    return {
        "id": f"pt:{value}",
        "type": "property_type",
        "value": value,
        "name": value,
        "label": value,
        "city": "",
        "state": "",
        "country": "",
        "slug": None,
        "score": None,
    }


class LocationAutocompleteAPIView(APIView):
    """
    GET /api/locations/autocomplete/?q=<text>&limit=<n>

    Semantic (pgvector) location lookup. Empty ``q`` falls back to the
    alphabetical list so the widget can show initial suggestions.
    """

    def get(self, request):
        params = AutocompleteQuerySerializer(data=request.query_params)
        params.is_valid(raise_exception=True)
        query = (params.validated_data.get("q") or "").strip()
        limit = params.validated_data["limit"]

        results = []

        # Property types come first — they are exact vocabulary matches.
        if query:
            results.extend(
                _serialize_property_type(pt)
                for pt in suggest_property_types(query, limit=limit)
            )
            locs = semantic_locations(query, limit=limit)
        else:
            locs = Location.objects.filter(is_active=True).order_by("name")[:limit]

        results.extend(_serialize_location(loc) for loc in locs)

        return Response({
            "query": query,
            "results": results[:limit],
        })


class PropertySearchAPIView(APIView):
    """
    GET /api/properties/ — merged search.

    The single ``q`` parameter is interpreted as a location/place (spatial
    search) or as free text (semantic search); see
    ``property.services.search_properties``.
    """

    def get(self, request):
        params = PropertySearchQuerySerializer(data=request.query_params)
        params.is_valid(raise_exception=True)
        data = params.validated_data

        qs, meta = search_properties(
            q=data.get("q") or "",
            location=data.get("location"),
            property_type=data.get("property_type") or None,
            min_price=data.get("min_price"),
            max_price=data.get("max_price"),
            bedrooms=data.get("bedrooms"),
        )

        paginator = Paginator(qs, data["page_size"])
        page = paginator.get_page(data["page"])

        serializer = PropertySerializer(
            page.object_list, many=True, context={"request": request},
        )
        resolved = meta.get("location")
        return Response({
            "count": paginator.count,
            "page": page.number,
            "num_pages": paginator.num_pages,
            "semantic": meta.get("semantic"),
            "spatial": meta.get("strategy"),
            "resolved_location": (
                LocationSerializer(resolved).data if resolved is not None else None
            ),
            "property_type": meta.get("property_type"),
            "results": serializer.data,
        })


class PropertyDetailAPIView(APIView):
    """GET /api/properties/<slug>/ — detail payload including nearby neighbours."""

    def get(self, request, slug):
        prop = get_object_or_404(
            Property.objects.select_related("location").prefetch_related("images"),
            slug=slug, is_active=True,
        )
        data = PropertySerializer(prop, context={"request": request}).data
        data["nearby"] = PropertySerializer(
            nearby_properties(prop, radius_km=5, limit=4),
            many=True, context={"request": request},
        ).data
        return Response(data)


class NearbyPropertiesAPIView(APIView):
    """GET /api/properties/<slug>/nearby/?radius_km=5&limit=4 — PostGIS DWithin."""

    def get(self, request, slug):
        prop = get_object_or_404(
            Property.objects.select_related("location"), slug=slug, is_active=True,
        )
        params = NearbyQuerySerializer(data=request.query_params)
        params.is_valid(raise_exception=True)

        neighbors = nearby_properties(
            prop,
            radius_km=params.validated_data["radius_km"],
            limit=params.validated_data["limit"],
        )
        serializer = PropertySerializer(
            neighbors, many=True, context={"request": request},
        )
        return Response({"count": len(serializer.data), "results": serializer.data})


class LocationListAPIView(APIView):
    """GET /api/locations/ — active locations, optionally filtered by boundary intersect."""

    def get(self, request):
        qs = Location.objects.filter(is_active=True)
        country = request.query_params.get("country")
        if country:
            qs = qs.filter(country__iexact=country)
        serializer = LocationSerializer(qs.order_by("name"), many=True)
        return Response({"count": qs.count(), "results": serializer.data})


class HealthAPIView(APIView):
    """GET /api/health/ — verifies DB, PostGIS and pgvector are reachable."""

    def get(self, request):
        from django.db import connection

        info = {"status": "ok"}
        try:
            with connection.cursor() as cur:
                cur.execute("SELECT PostGIS_Version();")
                info["postgis"] = cur.fetchone()[0]
                cur.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector';")
                row = cur.fetchone()
                info["pgvector"] = row[0] if row else None
        except Exception as exc:  # pragma: no cover - surfaced to the client
            return Response(
                {"status": "error", "detail": str(exc)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        info["locations"] = Location.objects.count()
        info["properties"] = Property.objects.count()
        return Response(info)
