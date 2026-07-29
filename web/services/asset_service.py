"""
web/services/asset_service.py

AssetService — wraps the CharacterRegistry for all asset operations,
plus category/search helpers.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Literal

from dharmapath.models.character import AgeState, CharacterEntry
from dharmapath.registry.registry import CharacterRegistry
from web.exceptions import NotFoundError, ConflictError
from web.schemas.assets import (
    AgeStateSchema,
    AssetCategory,
    AssetResponse,
    AssetSearchResponse,
    CreateAssetRequest,
)

logger = logging.getLogger(__name__)


def _to_schema(entry: CharacterEntry) -> AssetResponse:
    states_schema = {
        name: AgeStateSchema(
            reference_image=state.reference_image,
            reference_image_full=state.reference_image_full,
            candidates=state.candidates,
            appears_arcs=state.appears_arcs,
            is_approved=state.is_approved,
        )
        for name, state in entry.states.items()
    }
    # Infer thumbnail
    approved_state = entry.approved_state
    thumb = approved_state.reference_image if approved_state else None

    return AssetResponse(
        asset_id=entry.name,  # Until UUID registry is added, name is the ID
        name=entry.name,
        category="characters",      # Registry is currently character-only
        description=entry.description,
        status=entry.status,
        first_appears=entry.first_appears,
        ip_adapter_weight=entry.ip_adapter_weight,
        default_state=entry.default_state,
        states=states_schema,
        thumbnail_url=thumb,
    )


class AssetService:
    def __init__(self, registry: CharacterRegistry) -> None:
        self._registry = registry

    async def list_assets(self, category: AssetCategory | None = None) -> list[AssetResponse]:
        """List all assets. Currently backed by CharacterRegistry."""
        characters = self._registry.all()
        results = [_to_schema(c) for c in characters]
        if category and category != "characters":
            # Non-character categories are not in the registry yet
            return []
        return results

    async def get_asset(self, asset_id: str) -> AssetResponse:
        entry = self._registry.get(asset_id)
        if not entry:
            raise NotFoundError("Asset", asset_id)
        return _to_schema(entry)

    async def create_asset(self, request: CreateAssetRequest) -> AssetResponse:
        if self._registry.get(request.name):
            raise ConflictError(f"Asset '{request.name}' already exists in the registry.")

        entry = self._registry.register(
            name=request.name,
            description=request.description,
            first_appears=request.first_appears,
        )
        self._registry.save()
        return _to_schema(entry)

    async def search_assets(self, query: str, category: AssetCategory | None = None) -> AssetSearchResponse:
        q = query.lower()
        all_assets = await self.list_assets(category)
        results = [
            a for a in all_assets
            if q in a.name.lower() or q in a.description.lower()
        ]
        return AssetSearchResponse(
            results=results,
            total=len(results),
            query=query,
            category=category,
        )

    async def get_categories(self) -> list[dict]:
        """Return asset categories with counts."""
        chars = self._registry.all()
        return [
            {"category": "characters", "count": len(chars), "approved": sum(1 for c in chars if c.is_approved)},
            {"category": "locations", "count": 0, "approved": 0},
            {"category": "weapons", "count": 0, "approved": 0},
            {"category": "loras", "count": 0, "approved": 0},
        ]
