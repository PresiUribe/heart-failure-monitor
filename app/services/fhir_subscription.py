import asyncio
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


async def ensure_observation_subscription(max_attempts: int = 60, delay_seconds: int = 10) -> dict | None:
    """Ensure HAPI has an R4 REST-hook Subscription for Observation resources.

    This is intentionally best-effort: API startup must not fail if HAPI is still
    warming up. The function retries and returns None if it cannot create the
    subscription during the startup window.
    """
    base = settings.fhir_base_url.rstrip("/")
    callback = settings.fhir_subscription_callback
    headers = {"Accept": "application/fhir+json", "Content-Type": "application/fhir+json"}

    for attempt in range(1, max_attempts + 1):
        try:
            async with httpx.AsyncClient(timeout=settings.fhir_timeout_seconds) as client:
                # Reuse an existing subscription to avoid duplicates on restarts.
                search = await client.get(f"{base}/Subscription", headers=headers)
                if search.status_code < 400:
                    bundle = search.json()
                    for entry in bundle.get("entry", []) or []:
                        resource = entry.get("resource", {}) or {}
                        channel = resource.get("channel", {}) or {}
                        existing_headers = channel.get("header") or []
                        expected_header = f"X-FHIR-Hook-Secret: {settings.fhir_hook_secret}"
                        if (
                            channel.get("type") == "rest-hook"
                            and channel.get("endpoint") == callback
                            and expected_header in existing_headers
                            and resource.get("status") in {"requested", "active"}
                        ):
                            return resource

                subscription = {
                    "resourceType": "Subscription",
                    "status": "requested",
                    "reason": "Heart Failure Rural Monitoring - reactive Observation notifications",
                    "criteria": "Observation?status=registered,preliminary,final,amended,corrected,cancelled,entered-in-error,unknown",
                    "channel": {
                        "type": "rest-hook",
                        "endpoint": callback,
                        "payload": "application/fhir+json",
                        "header": [f"X-FHIR-Hook-Secret: {settings.fhir_hook_secret}"],
                    },
                }
                created = await client.post(f"{base}/Subscription", json=subscription, headers=headers)
                if created.status_code < 400:
                    resource = created.json() if created.content else subscription
                    logger.info("FHIR Observation Subscription ensured: %s", resource.get("id"))
                    return resource
                logger.warning(
                    "HAPI rejected Subscription on attempt %s: %s %s",
                    attempt,
                    created.status_code,
                    created.text[:500],
                )
        except Exception as exc:  # HAPI can take a while to start.
            logger.warning("FHIR Subscription setup attempt %s failed: %s", attempt, exc)

        if attempt < max_attempts:
            await asyncio.sleep(delay_seconds)

    return None
