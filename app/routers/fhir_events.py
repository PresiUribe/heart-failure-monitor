from fastapi import APIRouter, HTTPException, Request

from app.config import settings
from app.services.fhir_reactor import react_to_observation

router = APIRouter(prefix="/fhir/events", tags=["FHIR reactivo"])


@router.post("/observation")
async def observation_hook(request: Request):
    if request.headers.get("X-FHIR-Hook-Secret") != settings.fhir_hook_secret:
        raise HTTPException(status_code=403, detail="FHIR hook no autorizado")
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    resource = payload
    if payload.get("resourceType") == "Bundle" and payload.get("entry"):
        resource = payload["entry"][0].get("resource", {})

    return await react_to_observation(resource)
