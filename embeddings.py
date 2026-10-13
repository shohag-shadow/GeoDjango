from functools import lru_cache
from sentence_transformers import SentenceTransformer

MODEL_NAME = "all-MiniLM-L6-v2"
MAX_CHARS = 800


@lru_cache(maxsize=1)
def get_model() -> SentenceTransformer:
    return SentenceTransformer(MODEL_NAME)


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    truncated = [t[:MAX_CHARS] for t in texts]
    return get_model().encode(
        truncated, batch_size=64, show_progress_bar=False,
        convert_to_numpy=True, normalize_embeddings=True,
    ).tolist()