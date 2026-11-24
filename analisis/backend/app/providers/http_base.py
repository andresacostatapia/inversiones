"""Base para proveedores que se consultan por HTTP con API key."""

from __future__ import annotations

import httpx

from .base import MissingApiKey, ProviderError, RateLimited


class HttpProvider:
    name = "http"
    base_url = ""
    timeout = 15.0

    def __init__(self, api_key: str = "", client: httpx.Client | None = None):
        self.api_key = api_key or ""
        self._client = client

    def is_configured(self) -> bool:
        return bool(self.api_key)

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(base_url=self.base_url, timeout=self.timeout)
        return self._client

    def _get(self, path: str, params: dict) -> dict | list:
        if not self.api_key:
            raise MissingApiKey(f"{self.name}: falta la API key en .env")
        try:
            r = self.client.get(path, params=params)
        except httpx.HTTPError as e:
            raise ProviderError(f"{self.name}: error de red ({e.__class__.__name__})") from e
        if r.status_code == 429:
            raise RateLimited(f"{self.name}: el proveedor limitó las consultas (429)")
        if r.status_code in (401, 403):
            raise ProviderError(f"{self.name}: API key inválida o sin permiso ({r.status_code})")
        if r.status_code >= 400:
            raise ProviderError(f"{self.name}: respuesta {r.status_code}")
        try:
            return r.json()
        except ValueError as e:
            raise ProviderError(f"{self.name}: respuesta no es JSON") from e
