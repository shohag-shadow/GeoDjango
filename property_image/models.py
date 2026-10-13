from django.contrib.gis.db import models
from django.db.models import Q
from pgvector.django import HnswIndex, VectorField

from property.models import Property


def property_image_path(instance, filename):
    return f"properties/{instance.property_id}/{filename}"


class PropertyImage(models.Model):
    property = models.ForeignKey(
        Property,
        on_delete=models.CASCADE,
        related_name="images",
    )
    image = models.ImageField(upload_to=property_image_path)
    alt_text = models.CharField(max_length=255, blank=True)
    caption = models.TextField(blank=True)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    file_size = models.BigIntegerField(null=True, blank=True)
    embedding = VectorField(dimensions=768, null=True, blank=True)
    is_primary = models.BooleanField(default=False)
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "-created_at"]
        indexes = [
            models.Index(fields=["property", "sort_order"], name="prop_img_prop_sort_idx"),
            HnswIndex(
                name="prop_img_embedding_idx",
                fields=["embedding"],
                m=16, ef_construction=64,
                opclasses=["vector_cosine_ops"],
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["property"],
                condition=Q(is_primary=True),
                name="unique_primary_image_per_property",
            ),
        ]

    def __str__(self):
        return f"{self.property.title} — image {self.pk}"