from fastapi import APIRouter, HTTPException
from typing import List, Dict

router = APIRouter()

@router.post("/harris-matrix/validate")
async def validate_matrix(data: Dict):
    """Validate Harris matrix relationships"""
    nodes = data.get('nodes', [])
    edges = data.get('edges', [])
    errors = []
    
    # Validation logic
    if not nodes:
        errors.append("Matrix requires at least one node")
    
    for edge in edges:
        if edge['source'] not in [n['id'] for n in nodes]:
            errors.append(f"Source node {edge['source']} not found")
        if edge['target'] not in [n['id'] for n in nodes]:
            errors.append(f"Target node {edge['target']} not found")
    
    return {
        "success": len(errors) == 0,
        "data": {
            "valid": len(errors) == 0,
            "errors": errors
        }
    }

@router.post("/harris-matrix/relationships")
async def generate_relationships(data: Dict):
    """Generate stratigraphic relationships from Harris matrix"""
    relationships = []
    edges = data.get('edges', [])
    
    for edge in edges:
        relationships.append({
            "from_sample_id": edge['source'],
            "to_sample_id": edge['target'],
            "relationship": edge.get('relationship', 'above')
        })
    
    return {
        "success": True,
        "data": relationships
    }
