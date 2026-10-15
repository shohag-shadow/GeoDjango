from django.contrib.gis.db import models
from django.contrib.gis.db.models.functions import Distance
from django.contrib.gis.measure import D
from pgvector.django import CosineDistance, HnswIndex, VectorField


class PropertyQuerySet(models.QuerySet):
    """Reusable spatial/vector query helpers shared by views and the API."""

    def active(self):
        return self.filter(is_active=True)

    def within_radius(self, point, radius_km, annotate=False):
        """PostGIS DWithin: properties located within ``radius_km`` of ``point``."""
        if point is None:
            return self.none()
        qs = self.filter(point__isnull=False).filter(
            point__dwithin=(point, D(km=radius_km))
        )
        if annotate:
            qs = qs.annotate(distance=Distance("point", point))
        return qs

    def intersecting(self, geometry):
        """PostGIS Intersects: properties whose point/footprint falls inside a boundary."""
        if geometry is None:
            return self.none()
        return self.filter(
            models.Q(footprint__intersects=geometry)
            | models.Q(point__intersects=geometry)
        )

    def semantic(self, vector):
        """pgvector cosine-distance ordering for a query embedding."""
        if vector is None:
            return self.none()
        return (
            self.filter(embedding__isnull=False)
            .annotate(semantic_distance=CosineDistance("embedding", vector))
            .order_by("semantic_distance")
        )


class Property(models.Model):
    class Status(models.TextChoices):
        FOR_SALE = "for_sale", "For Sale"
        FOR_RENT = "for_rent", "For Rent"
        SOLD = "sold", "Sold"

    location = models.ForeignKey(
        "location.Location",
        on_delete=models.PROTECT,
        related_name="properties",
    )
    title = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    description = models.TextField(blank=True)
    amenities = models.JSONField(default=list, blank=True)
    property_type = models.CharField(max_length=100, blank=True)
    room_type = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=50, choices=Status.choices)
    price = models.DecimalField(max_digits=14, decimal_places=2)
    bedrooms = models.PositiveSmallIntegerField(default=0)
    beds = models.PositiveSmallIntegerField(default=0)
    bathrooms = models.PositiveSmallIntegerField(default=0)
    area_sqft = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    address = models.TextField(blank=True)
    # Remote image URL from the source data (rendered directly as an <img src>).
    image_url = models.URLField(max_length=500, blank=True)
    point = models.PointField(geography=True, srid=4326, null=True, blank=True)
    footprint = models.PolygonField(srid=4326, null=True, blank=True)
    embedding = VectorField(dimensions=384, null=True, blank=True)
    is_featured = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = PropertyQuerySet.as_manager()

    class Meta:
        verbose_name_plural = "properties"
        indexes = [
            models.Index(fields=["slug"]),
            models.Index(fields=["property_type", "status"]),
            models.Index(fields=["location", "status"]),
            HnswIndex(
                name="property_embedding_idx",
                fields=["embedding"],
                m=16, ef_construction=64,
                opclasses=["vector_cosine_ops"],
            ),
        ]

    def __str__(self):
        return self.title
