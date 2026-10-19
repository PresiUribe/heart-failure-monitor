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
