from functools import lru_cache

from sentence_transformers import SentenceTransformer

MODEL_NAME = "all-MiniLM-L6-v2"
MAX_CHARS = 800


@lru_cache(maxsize=1)
def get_model() -> SentenceTransformer:
    # CPU-only project: never try to use a GPU (no CUDA build is installed).
    return SentenceTransformer(MODEL_NAME, device="cpu")


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    truncated = [t[:MAX_CHARS] for t in texts]
    return get_model().encode(
        truncated, batch_size=64, show_progress_bar=False,
        convert_to_numpy=True, normalize_embeddings=True,
    ).tolist()


def _dedupe(parts) -> list[str]:
    """Keep the first occurrence of each non-empty, case-insensitive value."""
    seen = set()
    out = []
    for part in parts:
        value = (part or "").strip(" ,;:-")
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def location_document(location) -> str:
    """
    Text embedded for a place, so searching its name/location finds it.

    Covers the place's name and its full address chain (city, state, country).
    """
    return ", ".join(_dedupe([
        location.name,
        location.city,
        location.state,
        location.country,
    ]))


def property_document(prop) -> str:
    """
    Text embedded for a listing, so semantic search can match on anything a
    guest would type: the name, the place, the "About this place" description,
    the amenities and the room/bed layout (e.g. "2 room, king bed").
    """
    location = prop.location
    room_bits = []
    if prop.bedrooms:
        room_bits.append(f"{prop.bedrooms} bedroom{'s' if prop.bedrooms != 1 else ''}")
    if prop.beds:
        room_bits.append(f"{prop.beds} bed{'s' if prop.beds != 1 else ''}")
    if prop.bathrooms:
        room_bits.append(
            f"{prop.bathrooms} bathroom{'s' if prop.bathrooms != 1 else ''}"
        )

    header = _dedupe([
        prop.title,
        prop.property_type,
        prop.room_type,
        ", ".join(room_bits),
        prop.address,
        location.city if location else "",
        location.state if location else "",
        location.country if location else "",
        location.name if location else "",
    ])

    sections = [", ".join(header)]
    amenities = prop.amenities or []
    if amenities:
        sections.append("Amenities: " + ", ".join(str(a) for a in amenities))
    if prop.description:
        sections.append("About this place: " + prop.description)

    return "\n".join(s for s in sections if s)
