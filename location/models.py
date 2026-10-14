from django.contrib.gis.db import models
from pgvector.django import HnswIndex, VectorField


class Location(models.Model):
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    country = models.CharField(max_length=100)
    state = models.CharField(max_length=100, blank=True)
    city = models.CharField(max_length=100)
    address = models.TextField(blank=True)
    point = models.PointField(geography=True, srid=4326, null=True, blank=True)
    boundary = models.MultiPolygonField(srid=4326, null=True, blank=True)
    embedding = VectorField(dimensions=384, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["slug"]),
            models.Index(fields=["country", "state", "city"]),
            HnswIndex(
                name="location_embedding_idx",
                fields=["embedding"],
                m=16, ef_construction=64,
                opclasses=["vector_cosine_ops"],
            ),
        ]

    def __str__(self):
        return self.name