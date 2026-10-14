from pgvector.django import CosineDistance

from embeddings import embed_texts
from location.models import Location
from property.models import Property


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


def semantic_properties(query: str, limit: int = 24):
    """Properties ranked by cosine similarity to `query`."""
    query = (query or "").strip()
    if not query:
        return Property.objects.none()

    vector = embed_texts([query])[0]
    return (
        Property.objects
        .filter(is_active=True, embedding__isnull=False)
        .annotate(distance=CosineDistance("embedding", vector))
        .order_by("distance")
        .select_related("location")
        .prefetch_related("images")[:limit]
    )