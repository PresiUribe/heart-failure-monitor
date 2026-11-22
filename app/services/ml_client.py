import httpx
from app.config import settings


async def predict(payload: dict) -> dict:
    async with httpx.AsyncClient(timeout=settings.ml_timeout_seconds) as client:
        r = await client.post(f"{settings.ml_base_url.rstrip('/')}/predict", json=payload)
        r.raise_for_status()
        return r.json()


async def get_clusters() -> list[dict]:
    async with httpx.AsyncClient(timeout=settings.ml_timeout_seconds) as client:
        r = await client.get(f"{settings.ml_base_url.rstrip('/')}/clusters")
        r.raise_for_status()
        return r.json()


async def get_metrics() -> dict:
    async with httpx.AsyncClient(timeout=settings.ml_timeout_seconds) as client:
        r = await client.get(f"{settings.ml_base_url.rstrip('/')}/metrics")
        r.raise_for_status()
        return r.json()


async def embed(textos: list[str]) -> dict:
    """Embeddings del servicio ML: {"modelo", "dim", "vectores"} (normalizados, coseno = producto punto)."""
    async with httpx.AsyncClient(timeout=max(settings.ml_timeout_seconds, 60)) as client:
        r = await client.post(f"{settings.ml_base_url.rstrip('/')}/embed", json={"textos": textos})
        r.raise_for_status()
        return r.json()
