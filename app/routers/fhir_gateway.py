"""Public FHIR Observation gateway used for deterministic reactive demos.

The canonical server remains HAPI FHIR. Nginx routes the exact public
`POST /fhir/Observation` path through this endpoint. FastAPI forwards the
FHIR resource unchanged to HAPI and, only after HAPI accepts it, invokes the
same event reactor used by the HAPI Subscription callback. Other FHIR paths
still go directly to HAPI.

This is not polling: the reaction happens from the incoming clinical event.
The HAPI Subscription remains configured as the standards-based mechanism,
while this gateway makes the course's external POST test deterministic even
if a HAPI rest-hook delivery is delayed.
"""

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from app.config import settings
from app.services.fhir_reactor import react_to_observation

router = APIRouter(prefix="/fhir-gateway", tags=["FHIR reactivo"])


@router.post("/Observation")
async def post_observation(request: Request):
    try:
        submitted = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="FHIR Observation JSON inválida") from exc

    if submitted.get("resourceType") != "Observation":
        raise HTTPException(status_code=400, detail="Se esperaba resourceType=Observation")

    url = f"{settings.fhir_base_url.rstrip('/')}/Observation"
    headers = {"Content-Type": "application/fhir+json", "Accept": "application/fhir+json"}

    async with httpx.AsyncClient(timeout=settings.fhir_timeout_seconds) as client:
        response = await client.post(url, json=submitted, headers=headers)

    # Preserve HAPI's status and response body for the FHIR client/tester.
    if response.status_code >= 400:
        return Response(
            content=response.content,
            status_code=response.status_code,
            media_type=response.headers.get("content-type", "application/fhir+json"),
        )

    try:
        created = response.json()
    except Exception:
        created = dict(submitted)

    # HAPI normally returns the created resource. If not, recover the id from
    # Location so the Subscription callback can be de-duplicated later.
    resource_for_event = dict(submitted)
    if isinstance(created, dict):
        resource_for_event.update(created)
    if not resource_for_event.get("id"):
        location = response.headers.get("location") or response.headers.get("content-location") or ""
        if "/Observation/" in location:
            resource_for_event["id"] = location.split("/Observation/", 1)[1].split("/", 1)[0]

    # Do not fail a valid FHIR POST only because the notification layer has a
    # transient issue. The audit/notification path is best effort here; the
    # standard HAPI Subscription remains a second event path.
    try:
        await react_to_observation(resource_for_event)
    except Exception:
        pass

    if isinstance(created, dict):
        return JSONResponse(
            content=created,
            status_code=response.status_code,
            headers={
                k: v
                for k, v in response.headers.items()
                if k.lower() in {"location", "content-location", "etag", "last-modified"}
            },
        )
    return Response(
        content=response.content,
        status_code=response.status_code,
        media_type=response.headers.get("content-type", "application/fhir+json"),
    )
