from fastapi import APIRouter, HTTPException
from typing import List

router = APIRouter()

AVAILABLE_CURVES = [
    {
        "id": "intcal20",
        "name": "IntCal20",
        "description": "Northern Hemisphere terrestrial radiocarbon age calibration",
        "type": "terrestrial",
        "reference": "Reimer et al. 2020",
        "year": 2020,
        "data_points": 5141
    },
    {
        "id": "marine20",
        "name": "Marine20",
        "description": "Marine radiocarbon age calibration",
        "type": "marine",
        "reference": "Heaton et al. 2020",
        "year": 2020,
        "data_points": 1341
    },
    {
        "id": "shcal20",
        "name": "SHCal20",
        "description": "Southern Hemisphere terrestrial radiocarbon age calibration",
        "type": "southern_hemisphere",
        "reference": "Hogg et al. 2020",
        "year": 2020,
        "data_points": 5141
    }
]

@router.get("/curves")
async def list_curves(limit: int = 10):
    """List available calibration curves"""
    return {
        "success": True,
        "data": {
            "items": AVAILABLE_CURVES[:limit],
            "total": len(AVAILABLE_CURVES),
            "page": 1,
            "per_page": limit,
            "total_pages": 1
        }
    }

@router.get("/curves/{curve_id}")
async def get_curve(curve_id: str):
    """Get specific calibration curve"""
    for curve in AVAILABLE_CURVES:
        if curve["id"] == curve_id:
            return {"success": True, "data": curve}
    raise HTTPException(status_code=404, detail="Curve not found")

@router.get("/curves/type/{curve_type}")
async def get_curves_by_type(curve_type: str):
    """Get curves by type"""
    curves = [c for c in AVAILABLE_CURVES if c["type"] == curve_type]
    return {
        "success": True,
        "data": curves
    }
