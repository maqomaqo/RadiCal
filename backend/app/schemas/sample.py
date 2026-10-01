from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Optional, List, Literal, Dict, Any
from datetime import datetime

from app.services import curve_registry
from app.services.stratigraphy import expand_relationships, find_cycle

# The curves shipped with the app. Not the whole answer -- a user can import
# their own -- so what a request may name is "any curve the registry can
# resolve", checked by validate_curve_id below.
VALID_CURVE_IDS = curve_registry.BUILTIN_CURVE_IDS


def validate_curve_id(curve_id: Optional[str]) -> Optional[str]:
    """
    Reject a curve this app cannot load, at the edge, so a typo fails clearly
    rather than reaching get_curve(). Not a Pydantic `Literal`, because the
    set of curves is something a user can add to.

    Builtin ids are answered without a database query, so forty dates all on
    IntCal20 cost nothing.

    **This is a shape check, not an authorisation check.** A validator has no
    request and therefore no user, so it can only ask whether the id names a
    curve that exists anywhere. Whether the CALLER may name it is a separate
    question, asked by `api.curves.authorise_curve_ids` once the route knows
    who is asking -- the same division as everywhere else in this app, where
    schemas check shape and `api/auth.py` checks ownership.

    Leaving the ownership question to the route rather than doing both here is
    deliberate: a validator that rejected another user's curve id as "unknown"
    would produce a 422 listing every curve the caller CAN use, which is a
    fine error message, but it would do it from a layer that cannot tell that
    case apart from a typo -- and the two want different wording.
    """
    if curve_id is None:
        return None
    if not curve_registry.exists(curve_id, curve_registry.SCOPE_ANY):
        # Only the builtins are named. Listing every stored id would name the
        # imports of every account on the server, which is exactly what
        # per-user curves exist to prevent -- and this layer cannot narrow the
        # list to the caller's own, having no caller. The route's own refusal
        # (authorise_curve_ids) does name the curves you can actually use.
        builtins = ', '.join(curve_registry.BUILTIN_CURVE_IDS)
        raise ValueError(
            f"Unknown calibration curve '{curve_id}'. This app ships with "
            f"{builtins}, and calibrates against any curve you have imported. "
            "A curve it does not have cannot be substituted for one it does."
        )
    return curve_id


class MeasurementInput(BaseModel):
    """
    A single radiocarbon measurement.

    The numeric bounds are generous sanity checks, not claims about
    scientific validity: they catch data-entry mistakes (an extra digit, a
    calendar year pasted into the BP field, a sign error) rather than
    gatekeeping legitimate edge-case measurements.
    """
    sample_name: str = Field(default="Sample", max_length=200)
    age_bp: float = Field(
        ..., ge=0, le=60000,
        description="Radiocarbon age in years BP. Must be non-negative "
                     "and within the range calibration curves cover "
                     "(IntCal20/Marine20/SHCal20 span 0-55,000 cal BP)."
    )
    error_1sigma: float = Field(
        ..., gt=0, le=10000,
        description="1-sigma measurement uncertainty in years. Must be "
                     "positive -- zero or negative error is not a valid "
                     "measurement uncertainty."
    )
    material_type: str = Field(default="unknown", max_length=100)
    # Unset by default rather than "terrestrial", so the API layer can infer
    # "marine" from curve_id when the caller does not say.
    sample_type: Optional[Literal["terrestrial", "marine"]] = None
    curve_id: str = "intcal20"

    @field_validator('curve_id')
    @classmethod
    def _known_curve(cls, value: str) -> str:
        return validate_curve_id(value)
    # Local marine reservoir offset (Delta-R) and its 1-sigma uncertainty, in
    # radiocarbon years. A marine curve already models the GLOBAL average
    # reservoir effect, so only the local deviation from it belongs here;
    # entering a whole reservoir age would count the global effect twice.
    #
    # Sign convention: a marine sample of calendar age t has expected
    # radiocarbon age curve(t) + delta_r, so a positive Delta-R dates the
    # sample YOUNGER than it would uncorrected. Supersedes `reservoir_age`.
    delta_r: Optional[float] = Field(default=None, ge=-5000, le=5000)
    delta_r_error: Optional[float] = Field(default=None, ge=0, le=5000)
    # Where a looked-up ΔR came from. Carried through the draft for the same
    # reason the coordinates are: an edit resubmits whatever it holds, so a
    # derivation missing here is a derivation deleted by editing the analysis
    # it belongs to. Null means hand-entered, which is the honest answer.
    delta_r_source: Optional[Dict[str, Any]] = None

    # Where the sample came from, in WGS84 decimal degrees. The bounds are the
    # real ones, not sanity checks: there is no latitude of 91. Degrees and
    # minutes are not accepted in any spelling -- the importer refuses such a
    # cell by name rather than guessing whether 54.30 means 54.5 or 54.3.
    latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    longitude: Optional[float] = Field(default=None, ge=-180, le=180)

    reservoir_age: Optional[float] = Field(default=None, ge=-500, le=2000)
    depth_cm: Optional[float] = Field(default=None, ge=0, le=10000)

    # Where this sample sits in a wiggle-matched series, in years BEFORE the
    # series reference point: 0 AT the reference and INCREASING INTO THE PAST,
    # so a member's calendar age is the reference's PLUS its offset. Negative
    # is allowed and means a member younger than the reference, which is what
    # a series measured from a mid-series ring looks like.
    #
    # A separate field from depth_cm, not a reuse of it: rings and depth are
    # different units running in opposite directions. See
    # services/wiggle_match.py, which states the convention once.
    ring_offset: Optional[float] = Field(default=None, ge=-50000, le=50000)
    # The gap's own 1-sigma uncertainty, in the same units -- OxCal's
    # V_Sequence, where a gap was measured rather than counted. Absent or zero
    # is a D_Sequence: the gaps are asserted exactly.
    ring_offset_error: Optional[float] = Field(default=None, ge=0, le=10000)

    location: Optional[str] = Field(default=None, max_length=500)
    notes: Optional[str] = Field(default=None, max_length=2000)

    # Age limiters in cal BP. tpq_age_bp ("terminus post quem") is a MAXIMUM
    # age bound, taq_age_bp ("terminus ante quem") a MINIMUM, so the valid
    # range is [taq_age_bp, tpq_age_bp] -- see _bounds_not_contradictory.
    tpq_age_bp: Optional[float] = Field(default=None, ge=0, le=60000)
    taq_age_bp: Optional[float] = Field(default=None, ge=0, le=60000)

    @model_validator(mode="after")
    def _gap_error_belongs_to_a_gap(self):
        """
        A gap uncertainty with no gap under it describes nothing. Refused
        rather than ignored, because the field it would have belonged to is
        the one thing a wiggle match cannot work without.
        """
        if self.ring_offset_error is not None and self.ring_offset is None:
            raise ValueError(
                "ring_offset_error cannot be set on its own -- it is the "
                "uncertainty on a gap, so send the gap it belongs to"
            )
        return self

    @field_validator("sample_name")
    @classmethod
    def _sample_name_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("sample_name cannot be blank")
        return v

    @model_validator(mode="after")
    def _bounds_not_contradictory(self):
        if self.tpq_age_bp is not None and self.taq_age_bp is not None:
            if self.taq_age_bp > self.tpq_age_bp:
                raise ValueError(
                    "taq_age_bp (minimum age -- terminus ante quem) cannot be "
                    "greater than tpq_age_bp (maximum age -- terminus post "
                    "quem); the valid range [taq_age_bp, tpq_age_bp] would be "
                    "empty"
                )
        return self

    @model_validator(mode="after")
    def _delta_r_has_an_uncertainty(self):
        """
        Delta-R is always published with an uncertainty and combined into
        the measurement's error in quadrature. Accepting one without the
        other would report a date more precise than the evidence supports.
        """
        if self.delta_r is not None and self.delta_r_error is None:
            raise ValueError(
                "delta_r_error is required when delta_r is given -- a "
                "reservoir offset has its own uncertainty, and omitting it "
                "would understate the uncertainty of the calibrated date"
            )
        return self

    @model_validator(mode="after")
    def _coordinates_come_in_pairs(self):
        """
        Half a coordinate is not a usable fact, for the same reason half a
        Delta-R is not: nothing downstream can do anything with a latitude
        whose longitude is missing, and a date that looks located but is not
        would quietly drop out of every map and every distance the app
        computes. Refused at the door instead.
        """
        if (self.latitude is None) != (self.longitude is None):
            missing = 'longitude' if self.longitude is None else 'latitude'
            raise ValueError(
                f"{missing} is required when the other half of the coordinate "
                "is given -- a position needs both, and a half-located date "
                "would be silently dropped from anything that uses one"
            )
        return self


class CalibrateRequest(MeasurementInput):
    """Request body for POST /analysis/calibrate."""
    project_id: Optional[str] = None
    save: bool = True


class ProjectSampleInput(MeasurementInput):
    """
    A date being added to a PROJECT's collection (models.ProjectSample):
    every MeasurementInput check, plus the deploy-time context default.
    """
    default_context_name: Optional[str] = Field(default=None, max_length=200)

    @field_validator("default_context_name")
    @classmethod
    def _blank_context_is_none(cls, v: Optional[str]) -> Optional[str]:
        # "" is "no context", not a context literally named "".
        if v is None:
            return None
        v = v.strip()
        return v or None


class ProjectSampleUpdate(BaseModel):
    """
    PATCH body for a date in a project's collection -- every field optional.
    Constraints mirror ProjectSampleInput; the TPQ/TAQ ordering check cannot
    be repeated here (a partial update may carry only one of the pair) and is
    applied against the merged result in the endpoint.
    """
    sample_name: Optional[str] = Field(default=None, max_length=200)
    age_bp: Optional[float] = Field(default=None, ge=0, le=60000)
    error_1sigma: Optional[float] = Field(default=None, gt=0, le=10000)
    material_type: Optional[str] = Field(default=None, max_length=100)
    sample_type: Optional[Literal["terrestrial", "marine"]] = None
    curve_id: Optional[str] = None

    @field_validator('curve_id')
    @classmethod
    def _known_curve(cls, value: Optional[str]) -> Optional[str]:
        return validate_curve_id(value)
    reservoir_age: Optional[float] = Field(default=None, ge=-500, le=2000)
    delta_r: Optional[float] = Field(default=None, ge=-5000, le=5000)
    delta_r_error: Optional[float] = Field(default=None, ge=0, le=5000)
    # Like the TPQ/TAQ pair above, the both-or-neither rule cannot live here --
    # a PATCH may legitimately carry only a longitude, correcting a typo in one
    # half of a coordinate that is already complete. It is applied to the
    # MERGED row in api/project_samples.update_project_sample.
    latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    longitude: Optional[float] = Field(default=None, ge=-180, le=180)
    depth_cm: Optional[float] = Field(default=None, ge=0, le=10000)
    # See MeasurementInput for the sign convention.
    ring_offset: Optional[float] = Field(default=None, ge=-50000, le=50000)
    ring_offset_error: Optional[float] = Field(default=None, ge=0, le=10000)
    location: Optional[str] = Field(default=None, max_length=500)
    notes: Optional[str] = Field(default=None, max_length=2000)
    default_context_name: Optional[str] = Field(default=None, max_length=200)
    tpq_age_bp: Optional[float] = Field(default=None, ge=0, le=60000)
    taq_age_bp: Optional[float] = Field(default=None, ge=0, le=60000)

    @field_validator("sample_name")
    @classmethod
    def _sample_name_not_blank(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        if not v:
            raise ValueError("sample_name cannot be blank")
        return v


class ProjectSampleBulkInput(BaseModel):
    """
    POST body for adding dates to a project's collection -- a list, so the
    UI's "paste several dates at once" path is a single request.
    """
    samples: List[ProjectSampleInput] = Field(..., min_length=1, max_length=500)


class ProjectSampleBulkUpdate(BaseModel):
    """
    PATCH body for applying one value across a selection of a project's
    dates -- the calibration curve, a Delta-R shared by everything from one
    site or water body, or the coordinate those dates share, all of which are
    per-provenance rather than per-date.

    Whether a field was SENT matters, not whether it is null: `delta_r: null`
    clears the offset, omitting the key leaves it alone. Pydantic records that
    in `model_fields_set`, so no sentinel is needed -- see
    api/project_samples.bulk_update_project_samples.
    """
    sample_ids: List[str] = Field(..., min_length=1, max_length=500)
    curve_id: Optional[str] = None
    delta_r: Optional[float] = Field(default=None, ge=-5000, le=5000)
    delta_r_error: Optional[float] = Field(default=None, ge=0, le=5000)
    # Dates from one site share a position, which is why placing a pin writes
    # one coordinate across a whole selection rather than n times over.
    latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    longitude: Optional[float] = Field(default=None, ge=-180, le=180)

    # Where a looked-up ΔR came from, as api/reservoir.lookup_delta_r returned
    # it. Only meaningful alongside a delta_r in the same request: sent on its
    # own it would attach a derivation to a number it did not derive.
    #
    # Its absence is load-bearing. A delta_r arriving WITHOUT one is a hand
    # edit, and the endpoint clears any provenance already stored -- see
    # api/project_samples.bulk_update_project_samples. Provenance that
    # survived an edit would be a lie about the number beside it.
    delta_r_source: Optional[dict] = None

    @field_validator('curve_id')
    @classmethod
    def _known_curve(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            raise ValueError("curve_id cannot be cleared -- every date needs a curve")
        return validate_curve_id(value)

    @field_validator("sample_ids")
    @classmethod
    def _no_duplicate_ids(cls, v: List[str]) -> List[str]:
        if len(set(v)) != len(v):
            raise ValueError("sample_ids contains a duplicate id")
        return v

    @model_validator(mode="after")
    def _something_to_do(self):
        if not ({'curve_id', 'delta_r', 'delta_r_error',
                 'latitude', 'longitude'} & self.model_fields_set):
            raise ValueError(
                "nothing to apply -- send curve_id, delta_r, a coordinate, "
                "or any combination of them"
            )
        return self

    @model_validator(mode="after")
    def _delta_r_has_an_uncertainty(self):
        """
        Same rule as MeasurementInput's, applied across a whole selection.
        Clearing is symmetrical -- `delta_r: null` clears the error too, so no
        stray uncertainty is left with no offset to belong to.
        """
        sent = self.model_fields_set
        if 'delta_r' in sent and self.delta_r is not None and self.delta_r_error is None:
            raise ValueError(
                "delta_r_error is required when delta_r is given -- a "
                "reservoir offset has its own uncertainty, and omitting it "
                "would understate the uncertainty of every date it is "
                "applied to"
            )
        if 'delta_r_error' in sent and 'delta_r' not in sent:
            raise ValueError(
                "delta_r_error cannot be set on its own -- send the offset it "
                "belongs to"
            )
        return self

    @model_validator(mode="after")
    def _provenance_belongs_to_an_offset(self):
        """
        A derivation with no derived value attached to it describes nothing.

        Refused rather than ignored, because the mistake it catches is the
        expensive one: provenance applied on its own would sit beside whatever
        ΔR happened to already be there and claim to explain it.
        """
        sent = self.model_fields_set
        if 'delta_r_source' in sent and self.delta_r_source is not None:
            if 'delta_r' not in sent or self.delta_r is None:
                raise ValueError(
                    "delta_r_source cannot be sent without the delta_r it "
                    "describes -- a derivation with no derived value beside "
                    "it would claim to explain whatever was already there")
        return self

    @model_validator(mode="after")
    def _coordinates_come_in_pairs(self):
        """
        Same rule as MeasurementInput's, applied across a whole selection --
        and here it CAN be a schema check rather than a merged-row one, because
        a bulk update writes a position onto every date in the selection rather
        than correcting one that is already there. Sending one half would
        half-locate every date it touched.

        Clearing is symmetrical: `latitude: null, longitude: null` unplaces the
        selection, and null in only one of them is still half a coordinate.
        """
        sent = self.model_fields_set
        if ('latitude' in sent) != ('longitude' in sent):
            missing = 'longitude' if 'longitude' not in sent else 'latitude'
            raise ValueError(
                f"{missing} is required when the other half of the coordinate "
                "is given -- a bulk update writes a position onto every date "
                "in the selection, and half of one is not a position"
            )
        if 'latitude' in sent and (self.latitude is None) != (self.longitude is None):
            raise ValueError(
                "a coordinate is cleared as a pair -- send both latitude and "
                "longitude as null, or neither"
            )
        return self


class ProjectSampleBulkDelete(BaseModel):
    """
    DELETE body for removing several dates from a project's collection.
    Analyses deployed from them keep their own copies (models.ProjectSample).
    """
    sample_ids: List[str] = Field(..., min_length=1, max_length=500)

    @field_validator("sample_ids")
    @classmethod
    def _no_duplicate_ids(cls, v: List[str]) -> List[str]:
        if len(set(v)) != len(v):
            raise ValueError("sample_ids contains a duplicate id")
        return v


class DeployRequest(BaseModel):
    """
    POST body for deploying a project's dates into a new analysis (see
    api/project_samples.py). The dates are COPIED, so the analysis is
    independent of the collection from then on.
    """
    name: str = Field(default="Untitled", max_length=200)
    sample_ids: List[str] = Field(..., min_length=1)
    analysis_type: Literal["bayesian", "calibration"] = "bayesian"

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        v = v.strip()
        return v or "Untitled"

    @field_validator("sample_ids")
    @classmethod
    def _no_duplicate_ids(cls, v: List[str]) -> List[str]:
        if len(set(v)) != len(v):
            raise ValueError("sample_ids contains a duplicate id")
        return v


class BayesianMeasurementInput(MeasurementInput):
    """A measurement within a Bayesian analysis request. Needs a
    client-supplied id so relationships can reference it."""
    id: str = Field(..., min_length=1)
    # Must match the id of one of the request's top-level `groups`; see
    # BayesianRequest._validate_stratigraphy.
    group_id: Optional[str] = None
    # The model-tree phase this date belongs to, when the request carries a
    # `model_tree`. Supersedes group_id; see BayesianRequest.
    element_id: Optional[str] = None
    # Prior probability this date does not belong to the population being
    # modelled -- residual charcoal, intrusive material, lab error. OxCal's
    # `Outlier("General", 0.05)`. When set, the date's likelihood becomes a
    # mixture of itself and a heavy-tailed alternative, and the result carries
    # the posterior probability it was the alternative (services/exact_chain).
    # Off by default because it changes reported dates.
    #
    # Allowed up to 1.0, which is how OxCal declares material residual
    # OUTRIGHT rather than suspecting it -- ordinary practice for charcoal
    # known to be old wood.
    outlier_prior: float = Field(default=0.0, ge=0.0, le=1.0)
    # "general" is OxCal's symmetric heavy-tailed default, for a date wrong in
    # an unknown way; "charcoal" is one-sided (the material formed before its
    # context, never after). See services/exact_chain._outlier_kernel.
    outlier_model: Literal["general", "charcoal"] = "general"


class SampleGroupInput(BaseModel):
    """
    A named stratigraphic phase/group within a Bayesian analysis request, so
    one relationship or limiter applies to every member at once. Needs a
    client-supplied id so relationships and measurements can reference it
    before anything is persisted.
    """
    id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1, max_length=200)
    tpq_age_bp: Optional[float] = Field(default=None, ge=0, le=60000)
    taq_age_bp: Optional[float] = Field(default=None, ge=0, le=60000)

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("group name cannot be blank")
        return v

    @model_validator(mode="after")
    def _bounds_not_contradictory(self):
        if self.tpq_age_bp is not None and self.taq_age_bp is not None:
            if self.taq_age_bp > self.tpq_age_bp:
                raise ValueError(
                    f"group '{self.name}': taq_age_bp (minimum age) cannot "
                    "be greater than tpq_age_bp (maximum age)"
                )
        return self


class RelationshipInput(BaseModel):
    """
    A stratigraphic constraint between two nodes in a Bayesian analysis. Each
    side is a measurement or a group, per from_type/to_type. The `relationship`
    Literal is what rejects a typo like "beneath", which the constraint
    builder would otherwise drop silently.
    """
    from_sample_id: str
    to_sample_id: str
    from_type: Literal["sample", "group"] = "sample"
    to_type: Literal["sample", "group"] = "sample"
    relationship: Literal["above", "below", "equal"]


class DispersalOrigin(BaseModel):
    """
    Where a wave of advance is hypothesised to have started.

    Both halves required, unlike a sample's coordinates: a date with half a
    position is a half-entered date, but an origin with half a position is not
    an origin at all -- there is nothing to measure a distance from.
    """
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)


class BayesianConfigInput(BaseModel):
    # Which solver to use.
    #
    #   auto   pick per model: integrate exactly where that is possible,
    #          otherwise sample. The right default -- exact integration has no
    #          sampler to converge and no calibration lobe to miss.
    #   exact  require exact integration, and FAIL rather than silently
    #          sampling if this model cannot be integrated. Refusing is the
    #          point: a request for an exact answer that quietly returns a
    #          sampled one is the silent-fallback problem all over again.
    #   gibbs  sample with OxCal's MH/Gibbs scheme even where exact
    #          integration was available.
    #
    # `gibbs` exists so an analyst can reproduce a published OxCal analysis,
    # or cross-check the exact solver against an independent route.
    solver: Literal["auto", "exact", "gibbs"] = "auto"
    # How many samples each date's posterior is REPORTED with, not how long
    # anything runs: a chain-shaped model is integrated exactly and has no run
    # length, and services/gibbs.py extends its own run until it converges
    # rather than taking a figure from here on trust.
    mcmc_samples: int = Field(default=4000, ge=100, le=20000)
    # Unused by either solver. Retained so a saved analysis's bayesian_config
    # still round-trips through an edit rather than being rejected.
    mcmc_tune: int = Field(default=1000, ge=100, le=20000)
    # Independent chains the sampler runs in parallel and pools, each with
    # its own seed. Above one, a chain settled in a different calibration
    # lobe from the others is reported as disagreement (gibbs.solve_chains);
    # one chain is the plain run. Ignored by the exact solver, which has no
    # chain.
    mcmc_chains: int = Field(default=4, ge=1, le=16)
    # OxCal's UniformSpanPrior, on by default there and here: it stops a
    # sequence's span prior depending on how many sub-groups it is divided
    # into (Nicholls and Jones 2001), and is the identity below three
    # boundaries. See exact_chain._span_prior_correction and
    # gibbs.Model._span_priors.
    uniform_span_prior: bool = True
    # The calendar step, in years, modelled HPD ranges and durations are
    # reported at: OxCal's Options Resolution, five years by default there and
    # here. Finer suits a recent model whose ranges span a few decades, coarser
    # a Pleistocene one. Reporting only -- the solver's own grid, and so every
    # posterior, agreement index and draw, is the same whatever is set; see
    # exact_chain.REPORTING_STEP_YEARS.
    resolution: int = Field(default=5, ge=1, le=100)
    # Also estimate how long each sequence, phase and set of dated events
    # lasted, and how far apart the phases sit (services/intervals.py).
    #
    # Opt-in rather than always-on, and not for cost: a phase duration asserts
    # the dates are a fair sample of a bounded episode, which is the analyst's
    # modelling assumption to make. These are also the only figures here
    # carrying Monte Carlo error, since they can only be read off JOINT draws.
    estimate_durations: bool = False
    # Also summarise how each phase's dates are spread through time as a
    # kernel density estimate (services/kde.py, OxCal's KDE_Plot used in place
    # of Phase): from the solver's joint draws, with the kernels reflected at
    # the phase's Boundaries. Opt-in as group calibration's is: it reads the
    # dates as a sample of one population of events.
    estimate_kde: bool = False
    # Also fit the spatial models: how fast the thing being dated moved across
    # the landscape, and which way (services/dispersal.py).
    #
    # Opt-in for the durations reason and one more. OLS on spatially
    # autocorrelated residuals will always return a tidy speed with a tidy
    # interval, whether or not the dates describe a front at all, so this must
    # be a claim somebody made deliberately rather than a number that appears
    # beside every analysis with coordinates on it.
    estimate_dispersal: bool = False
    # Where the front is hypothesised to have started.
    #
    # OPTIONAL, and what it buys is a second model rather than a complete one.
    # The trend surface assumes no origin -- that is exactly what makes it the
    # right first look at a dataset, before a hypothesis has been imposed on
    # it -- so a dispersal request with no pin is a request for the trend
    # alone, not a half-finished one. Placing a pin adds the wave of advance,
    # which IS measured from somewhere: moving the origin moves its speed, and
    # an app that picked one silently would be inventing the hypothesis rather
    # than testing it. Inferring the origin from the data is a separate thing
    # again, with a separate output -- a likelihood surface, not a point.
    dispersal_origin: Optional[DispersalOrigin] = None


class GroupCalibrationRequest(BaseModel):
    """
    Request body for POST /analysis/calibrate-group: unmodelled calibration
    of several independent measurements, saved as one analysis so they share
    the Results page's plot and table -- one timeline, no stratigraphic
    relationship asserted. See BayesianRequest for that.

    Reuses BayesianMeasurementInput so the UI can send the measurement objects
    it already builds for a Bayesian draft unchanged. Its
    group_id/element_id/outlier_* fields are accepted but not applied: a group
    calibration has no phases and no outlier model to be an outlier from.
    """
    name: str = Field(default="Group Calibration", max_length=200)
    measurements: List[BayesianMeasurementInput] = Field(..., min_length=1)
    project_id: Optional[str] = None
    save: bool = True
    # Also report how many years apart every pair of these dates is and how
    # confidently they can be ordered. Only differences, since unrelated dates
    # have no phase and so no duration. Exact here (independent dates, so
    # drawing them separately IS drawing from the joint), and opt-in so thirty
    # dates do not answer with 435 pairs nobody asked for.
    estimate_durations: bool = False
    # Also summarise how the dates are spread through time: a kernel density
    # estimate, OxCal's KDE_Plot (services/kde.py). Opt-in because it reads
    # the dates as a sample of one population of events, which is the
    # analyst's claim to make, and a tidy curve appears either way.
    estimate_kde: bool = False

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        v = v.strip()
        return v or "Group Calibration"

    @field_validator("measurements")
    @classmethod
    def _no_duplicate_ids(cls, v: List["BayesianMeasurementInput"]) -> List["BayesianMeasurementInput"]:
        ids = [m.id for m in v]
        if len(set(ids)) != len(ids):
            raise ValueError("measurements contain a duplicate id")
        return v


class BayesianRequest(BaseModel):
    """Request body for POST /analysis/bayesian."""
    name: str = Field(default="Untitled", max_length=200)
    measurements: List[BayesianMeasurementInput] = Field(..., min_length=1)
    groups: List[SampleGroupInput] = Field(default_factory=list)
    relationships: List[RelationshipInput] = Field(default_factory=list)
    project_id: Optional[str] = None
    save: bool = True
    bayesian_config: BayesianConfigInput = Field(default_factory=BayesianConfigInput)
    # The OxCal-style model structure (services/model_tree.py). When present
    # it is authoritative and `groups`/`relationships` are ignored, with each
    # measurement's element_id naming its phase; when absent,
    # groups/relationships are converted into an equivalent tree server-side.
    model_tree: Optional[Dict[str, Any]] = None
    # What the analysis should BECOME, read only by PUT /analysis/{id}/inputs,
    # so changing a saved analysis's type converts it rather than leaving it
    # behind. None leaves the type as it is. POST /analysis/bayesian ignores
    # it -- that route creates a Bayesian analysis by definition.
    analysis_type: Optional[Literal["calibration", "group_calibration", "bayesian"]] = None

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        v = v.strip()
        return v or "Untitled"

    @model_validator(mode="after")
    def _validate_stratigraphy(self):
        """
        Cross-field checks needing the whole request: no duplicate ids, every
        group_id/relationship endpoint resolves, and the above/below
        constraints form no cycle. A cycle leaves no set of calendar ages able
        to satisfy every constraint, and would otherwise surface as an MCMC
        hang minutes into a run.
        """
        measurement_ids = [m.id for m in self.measurements]
        if len(set(measurement_ids)) != len(measurement_ids):
            raise ValueError("measurements contain a duplicate id")

        # A tree cannot contain a cycle, so it is validated on its own
        # structural rules instead.
        if self.model_tree is not None:
            from app.services.model_tree import validate_ordinates, validate_tree
            # Enough of each measurement for both checks: the structural one
            # needs the element assignment, and the ordinate one needs the
            # ordinate. Sending a wiggle match with a gap missing has to fail
            # HERE, at the edge, rather than minutes into a run.
            described = [
                {'id': m.id, 'sample_name': m.sample_name,
                 'element_id': m.element_id,
                 'ring_offset': m.ring_offset,
                 'ring_offset_error': m.ring_offset_error,
                 'depth_cm': m.depth_cm,
                 'outlier_prior': m.outlier_prior}
                for m in self.measurements
            ]
            validate_tree(self.model_tree, described)
            validate_ordinates(self.model_tree, described)
            return self

        group_ids = [g.id for g in self.groups]
        if len(set(group_ids)) != len(group_ids):
            raise ValueError("groups contain a duplicate id")

        measurement_id_set = set(measurement_ids)
        group_id_set = set(group_ids)
        if measurement_id_set & group_id_set:
            raise ValueError("a measurement id and a group id collide -- ids must be unique across both")

        for m in self.measurements:
            if m.group_id is not None and m.group_id not in group_id_set:
                raise ValueError(f"measurement '{m.id}' references unknown group_id '{m.group_id}'")

        def _check_endpoint(node_id: str, node_type: str, where: str):
            pool = group_id_set if node_type == "group" else measurement_id_set
            if node_id not in pool:
                raise ValueError(f"relationship {where} references unknown {node_type} id '{node_id}'")

        for r in self.relationships:
            _check_endpoint(r.from_sample_id, r.from_type, "from_sample_id")
            _check_endpoint(r.to_sample_id, r.to_type, "to_sample_id")

        measurements_dicts = [m.model_dump() for m in self.measurements]
        groups_dicts = [g.model_dump() for g in self.groups]
        relationships_dicts = [r.model_dump() for r in self.relationships]

        older_younger, _equal = expand_relationships(measurements_dicts, groups_dicts, relationships_dicts)
        cycle_nodes = find_cycle(older_younger)
        if cycle_nodes:
            names_by_id = {m.id: m.sample_name for m in self.measurements}
            labels = [names_by_id.get(n, n) for n in cycle_nodes]
            raise ValueError(
                "stratigraphic relationships are contradictory (a circular "
                f"above/below chain involving: {', '.join(labels)}) -- no "
                "set of calendar ages could satisfy every constraint at once"
            )

        return self


# --- Legacy aliases ---

class RadiocarbonMeasurementCreate(MeasurementInput):
    pass


class RadiocarbonMeasurement(RadiocarbonMeasurementCreate):
    id: str
    analysis_id: str
    created_at: datetime

    class Config:
        from_attributes = True
