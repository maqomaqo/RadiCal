"""
Request bodies for the curve manager.

The editorial half of a curve is the only part that can be changed after
import -- its numbers are immutable by design (see
models.StoredCalibrationCurve), so there is deliberately no schema here for
editing them.
"""
from typing import List, Literal, Optional
import re

from pydantic import BaseModel, Field, field_validator

# What a curve id may look like. It is a primary key, it appears in URLs, and
# it is what every RadiocarbonSample row stores, so it is kept to the same
# shape as the builtin ids rather than allowing arbitrary text.
SLUG_PATTERN = re.compile(r'^[a-z0-9][a-z0-9_-]{1,63}$')

CURVE_TYPES = ('terrestrial', 'marine', 'southern_hemisphere', 'mixed')


def slugify(text: str) -> str:
    """A curve id from its name: 'IntCal13' -> 'intcal13'."""
    slug = re.sub(r'[^a-z0-9]+', '-', (text or '').lower()).strip('-')
    return slug[:64]


class CurveMetadata(BaseModel):
    """
    The parts of a stored curve a user can write, at import and afterwards.

    `aliases` are the other spellings the curve answers to when read out of a
    file -- importing IntCal13 under the alias 'IntCal13.14c' is what lets an
    OxCal model written against it import rather than being refused.
    """
    name: str = Field(..., min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=2000)
    reference: Optional[str] = Field(default=None, max_length=500)
    doi: Optional[str] = Field(default=None, max_length=200)
    type: Literal[CURVE_TYPES] = 'terrestrial'
    # Which sample type this curve suits, and so what the frontend switches to
    # when "Marine" is selected. Distinct from `type` because IntCal20 and
    # SHCal20 are both terrestrial curves for terrestrial samples but are not
    # interchangeable.
    sample_type: Literal['terrestrial', 'marine'] = 'terrestrial'
    aliases: List[str] = Field(default_factory=list, max_length=20)

    @field_validator('aliases')
    @classmethod
    def _clean_aliases(cls, value: List[str]) -> List[str]:
        return [a.strip() for a in value if a and a.strip()]


class CurveMetadataUpdate(BaseModel):
    """Every field optional: a PATCH that names only `description` must not
    blank the citation beside it."""
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=2000)
    reference: Optional[str] = Field(default=None, max_length=500)
    doi: Optional[str] = Field(default=None, max_length=200)
    type: Optional[Literal[CURVE_TYPES]] = None
    sample_type: Optional[Literal['terrestrial', 'marine']] = None
    aliases: Optional[List[str]] = Field(default=None, max_length=20)


class MixedCurveRequest(BaseModel):
    """
    A curve defined as a proportional mixture of two others -- OxCal's
    Mix_Curves.

    The proportion is stated as a percentage of the FIRST curve, as OxCal
    states it, so a recipe copied from an .oxcal file means the same thing
    here. Its uncertainty is not optional in practice: a dietary fraction is
    estimated, and an estimate offered without its error produces a mixed
    curve that is more confident than the diet it came from.
    """
    curve_id: Optional[str] = None
    name: Optional[str] = Field(default=None, max_length=120)
    a: str = Field(..., description="The first curve's id")
    b: str = Field(..., description="The second curve's id")
    percent_a: float = Field(
        ..., ge=0, le=100,
        description="Percentage of the carbon from the first curve",
    )
    percent_a_error: float = Field(
        default=0.0, ge=0, le=100,
        description="1-sigma uncertainty on that percentage",
    )
    description: Optional[str] = Field(default=None, max_length=2000)
    reference: Optional[str] = Field(default=None, max_length=500)
    aliases: List[str] = Field(default_factory=list, max_length=20)

    @field_validator('aliases')
    @classmethod
    def _clean_aliases(cls, value: List[str]) -> List[str]:
        return [a.strip() for a in value if a and a.strip()]


class SimulateRequest(BaseModel):
    """
    A date asked for by calendar year rather than by measurement: what would a
    sample from this year look like, measured this well?
    """
    cal_age: float = Field(..., ge=0, le=60000, description="Calendar age in cal BP")
    error: float = Field(
        default=25.0, gt=0, le=10000,
        description="Laboratory 1-sigma on the hypothetical measurement",
    )
    delta_r: Optional[float] = Field(default=None, ge=-5000, le=5000)
    delta_r_error: Optional[float] = Field(default=None, ge=0, le=5000)
