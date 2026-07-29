"""web/routes/assets.py — Asset and character registry management."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, status

from web.dependencies import get_asset_service, require_api_key
from web.schemas.assets import (
    AssetCategory,
    AssetResponse,
    AssetSearchResponse,
    CreateAssetRequest,
)
from web.schemas.common import SuccessResponse
from web.services.asset_service import AssetService

router = APIRouter(prefix="/assets", tags=["Assets"])
_auth = Depends(require_api_key)


@router.get(
    "",
    response_model=SuccessResponse[list[AssetResponse]],
    summary="List all assets",
)
async def list_assets(
    category: Optional[AssetCategory] = Query(None, description="Filter by asset category"),
    asset_svc: AssetService = Depends(get_asset_service),
    _: str = _auth,
) -> SuccessResponse[list[AssetResponse]]:
    assets = await asset_svc.list_assets(category)
    return SuccessResponse(data=assets)


@router.get(
    "/categories",
    response_model=SuccessResponse[list[dict]],
    summary="List asset categories with counts",
)
async def list_categories(
    asset_svc: AssetService = Depends(get_asset_service),
    _: str = _auth,
) -> SuccessResponse[list[dict]]:
    cats = await asset_svc.get_categories()
    return SuccessResponse(data=cats)


@router.get(
    "/search",
    response_model=SuccessResponse[AssetSearchResponse],
    summary="Search assets by name or description",
)
async def search_assets(
    q: str = Query(..., min_length=1, description="Search query"),
    category: Optional[AssetCategory] = Query(None),
    asset_svc: AssetService = Depends(get_asset_service),
    _: str = _auth,
) -> SuccessResponse[AssetSearchResponse]:
    result = await asset_svc.search_assets(q, category)
    return SuccessResponse(data=result)


@router.get(
    "/{asset_id}",
    response_model=SuccessResponse[AssetResponse],
    summary="Get a single asset",
)
async def get_asset(
    asset_id: str,
    asset_svc: AssetService = Depends(get_asset_service),
    _: str = _auth,
) -> SuccessResponse[AssetResponse]:
    asset = await asset_svc.get_asset(asset_id)
    return SuccessResponse(data=asset)


@router.post(
    "",
    response_model=SuccessResponse[AssetResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Register a new asset",
)
async def create_asset(
    request: CreateAssetRequest,
    asset_svc: AssetService = Depends(get_asset_service),
    _: str = _auth,
) -> SuccessResponse[AssetResponse]:
    asset = await asset_svc.create_asset(request)
    return SuccessResponse(data=asset)
