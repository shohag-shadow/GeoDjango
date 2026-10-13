from django.contrib.gis.db import models
from pgvector.django import HnswIndex, VectorField


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
    slug = models.SlugField(unique=True)
    description = models.TextField(blank=True)
    property_type = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=50, choices=Status.choices)
    price = models.DecimalField(max_digits=14, decimal_places=2)
    bedrooms = models.PositiveSmallIntegerField(default=0)
    bathrooms = models.PositiveSmallIntegerField(default=0)
    area_sqft = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    address = models.TextField(blank=True)
    point = models.PointField(geography=True, srid=4326, null=True, blank=True)
    footprint = models.PolygonField(srid=4326, null=True, blank=True)
    embedding = VectorField(dimensions=384, null=True, blank=True)
    is_featured = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

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