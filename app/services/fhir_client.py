from dataclasses import dataclass

import httpx

from app.config import settings


class FHIRServiceError(RuntimeError):
    pass


@dataclass
class FHIRResult:
    resource: dict
    created: bool


class FHIRClient:
    def __init__(self):
        self.base_url = settings.fhir_base_url.rstrip("/")
        self.timeout = settings.fhir_timeout_seconds

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        url = f"{self.base_url}/{path.lstrip('/')}"

        # Fusiona los headers por defecto con los headers específicos de la llamada.
        # Antes se enviaba `headers=` dos veces al usar POST/PUT, lo que provocaba
        # un TypeError y hacía fallar la sincronización desde el frontend.
        custom_headers = kwargs.pop("headers", {}) or {}
        headers = {
            "Accept": "application/fhir+json",
            **custom_headers,
        }

        try:
            response = httpx.request(
                method,
                url,
                timeout=self.timeout,
                headers=headers,
                **kwargs,
            )
        except httpx.HTTPError as exc:
            raise FHIRServiceError(
                f"No fue posible conectar con HAPI FHIR: {exc}"
            ) from exc

        if response.status_code >= 400:
            body = response.text[:2000]
            raise FHIRServiceError(
                f"HAPI FHIR respondió {response.status_code}: {body}"
            )

        return response

    @staticmethod
    def _resource_from_response(response: httpx.Response) -> dict:
        if response.content:
            try:
                resource = response.json()
            except ValueError:
                resource = {}
        else:
            resource = {}

        if not resource.get("id"):
            location = (
                response.headers.get("Location")
                or response.headers.get("Content-Location")
            )
            if location:
                parts = [p for p in location.split("?")[0].rstrip("/").split("/") if p]
                if "_history" in parts:
                    idx = parts.index("_history")
                    if idx >= 1:
                        resource["id"] = parts[idx - 1]
                elif parts:
                    resource["id"] = parts[-1]

        return resource

    def metadata(self) -> dict:
        return self._request("GET", "metadata").json()

    def get(self, resource_type: str, resource_id: str) -> dict:
        return self._request(
            "GET",
            f"{resource_type}/{resource_id}",
        ).json()

    def search(self, resource_type: str, params: dict) -> dict:
        return self._request(
            "GET",
            resource_type,
            params=params,
        ).json()

    def upsert(
        self,
        resource_type: str,
        resource: dict,
        resource_id: str | None = None,
    ) -> FHIRResult:
        fhir_headers = {
            "Content-Type": "application/fhir+json",
            "Prefer": "return=representation",
        }

        if resource_id:
            resource = dict(resource)
            resource["id"] = resource_id
            response = self._request(
                "PUT",
                f"{resource_type}/{resource_id}",
                json=resource,
                headers=fhir_headers,
            )
            return FHIRResult(
                resource=self._resource_from_response(response),
                created=False,
            )

        response = self._request(
            "POST",
            resource_type,
            json=resource,
            headers=fhir_headers,
        )
        return FHIRResult(
            resource=self._resource_from_response(response),
            created=True,
        )
