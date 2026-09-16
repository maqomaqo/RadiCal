from pydantic import BaseModel
from typing import Literal, Optional
from datetime import datetime

# How precisely this project's coordinates may leave the app. The bare number
# is the stored form so api/results.COORDINATE_PRECISIONS can read it; the
# degree sign and the approximate distance belong to the UI. The two lists are
# checked against each other in tests/test_exports.py -- a precision the
# exporter cannot apply must not be storable.
CoordinatePrecision = Literal['full', '0.01', '0.1', '1']


class ProjectCreate(BaseModel):
    name: str
    description: Optional[str] = None

class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    # Both are per-project privacy settings rather than metadata, and both are
    # deliberately absent from ProjectCreate: they are decisions made about a
    # project that has coordinates in it, not part of naming one.
    coordinate_precision: Optional[CoordinatePrecision] = None
    map_tiles_enabled: Optional[bool] = None

class Project(ProjectCreate):
    id: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
