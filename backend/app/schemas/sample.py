from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Optional, List, Literal, Dict, Any
from datetime import datetime

from app.services.stratigraphy import expand_relationships, find_cycle

# Kept in sync with app.services.calibration._CURVE_LOADERS -- these are
# the only curve_ids the calibration engine actually knows how to load.
# Previously an unrecognized curve_id wasn't rejected anywhere: it flowed
# straight through to get_curve(), which silently fell back to IntCal20
# rather than erroring, so a typo'd curve_id produced a real but wrong
# result instead of a clear failure.
VALID_CURVE_IDS = ("intcal20", "marine20", "shcal20")


class MeasurementInput(BaseModel):
    """
    A single radiocarbon measurement, with the validation that was
    previously entirely absent: age_bp/error_1sigma of zero or negative,
    blank sample names, and unrecognized curve_id/sample_type values all
    used to pass straight through to calibration and either produce
    nonsense results or a confusing downstream error (a bare KeyError or
    numpy exception) instead of a clear, descriptive 422.

    The numeric bounds are deliberately generous sanity checks, not
    claims about scientific validity -- they're here to catch data-entry
    mistakes (a typo'd extra digit, a calendar year pasted into the BP
    field, a sign error) rather than to gatekeep legitimate edge-case
    measurements.
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
    # Optional and unset by default (rather than defaulting to
    # "terrestrial" here) so the API layer can keep its existing
    # smart-default behavior of inferring "marine" from curve_id when the
    # caller doesn't specify a sample_type explicitly.
    sample_type: Optional[Literal["terrestrial", "marine"]] = None
    curve_id: Literal[VALID_CURVE_IDS] = "intcal20"
    # Local marine reservoir offset (Delta-R) and its 1-sigma uncertainty,
    # both in radiocarbon years.
    #
    # A marine curve already models the GLOBAL average reservoir effect, so
    # what a sample needs on top of it is only the local deviation from that
    # average -- which is what Delta-R is. Entering a whole reservoir age
    # here (400-500 years for much of the ocean) would count the global
    # effect twice and pull the result centuries too young.
    #
    # Sign follows the usual convention: a marine sample of calendar age t
    # has expected radiocarbon age curve(t) + delta_r, so a positive Delta-R
    # means the local water is more depleted than the global average and the
    # sample dates YOUNGER than it would uncorrected.
    #
    # Supersedes `reservoir_age`, which was recorded but never applied to
    # anything -- see models.RadiocarbonSample.
    delta_r: Optional[float] = Field(default=None, ge=-5000, le=5000)
    delta_r_error: Optional[float] = Field(default=None, ge=0, le=5000)
    reservoir_age: Optional[float] = Field(default=None, ge=-500, le=2000)
    depth_cm: Optional[float] = Field(default=None, ge=0, le=10000)
    location: Optional[str] = Field(default=None, max_length=500)
    notes: Optional[str] = Field(default=None, max_length=2000)

    # Optional age limiters, both in cal BP like every other age field here.
    # tpq_age_bp ("terminus post quem") is a MAXIMUM age bound -- the
    # sample cannot be OLDER than this. taq_age_bp ("terminus ante quem")
    # is a MINIMUM age bound -- the sample cannot be YOUNGER than this.
    # Valid range is [taq_age_bp, tpq_age_bp]; see _bounds_not_contradictory
    # below for the sanity check on that ordering.
    tpq_age_bp: Optional[float] = Field(default=None, ge=0, le=60000)
    taq_age_bp: Optional[float] = Field(default=None, ge=0, le=60000)

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
        Delta-R is always published as a value with an uncertainty, and it
        is combined into the measurement's error in quadrature. Accepting
        one without the other would silently treat the offset as exactly
        known and report a date more precise than the evidence supports.
        """
        if self.delta_r is not None and self.delta_r_error is None:
            raise ValueError(
                "delta_r_error is required when delta_r is given -- a "
                "reservoir offset has its own uncertainty, and omitting it "
                "would understate the uncertainty of the calibrated date"
            )
        return self


class CalibrateRequest(MeasurementInput):
    """Request body for POST /analysis/calibrate."""
    project_id: Optional[str] = None
    save: bool = True


class ProjectSampleInput(MeasurementInput):
    """
    A date being added to a PROJECT's date collection (see
    models.ProjectSample). Inherits every measurement-field check from
    MeasurementInput -- age/error bounds, known curve_id, non-blank name,
    and the TPQ/TAQ ordering check -- and adds the deploy-time context
    default.
    """
    default_context_name: Optional[str] = Field(default=None, max_length=200)

    @field_validator("default_context_name")
    @classmethod
    def _blank_context_is_none(cls, v: Optional[str]) -> Optional[str]:
        # Treat "" / whitespace as "no context" rather than creating a
        # context literally named "" when this date is deployed.
        if v is None:
            return None
        v = v.strip()
        return v or None


class ProjectSampleUpdate(BaseModel):
    """
    PATCH body for a date already in a project's collection -- every field
    optional so a caller can send just the one they're changing. Field
    constraints mirror ProjectSampleInput/MeasurementInput; the TPQ/TAQ
    ordering check can't be repeated here (a partial update may only carry
    one of the pair) and is instead applied against the merged result in
    the endpoint.
    """
    sample_name: Optional[str] = Field(default=None, max_length=200)
    age_bp: Optional[float] = Field(default=None, ge=0, le=60000)
    error_1sigma: Optional[float] = Field(default=None, gt=0, le=10000)
    material_type: Optional[str] = Field(default=None, max_length=100)
    sample_type: Optional[Literal["terrestrial", "marine"]] = None
    curve_id: Optional[Literal[VALID_CURVE_IDS]] = None
    reservoir_age: Optional[float] = Field(default=None, ge=-500, le=2000)
    depth_cm: Optional[float] = Field(default=None, ge=0, le=10000)
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


class DeployRequest(BaseModel):
    """
    POST body for deploying a selection of a project's dates into a new
    analysis (see api/project_samples.py). The dates are COPIED, so the
    resulting analysis is independent of the collection from then on.
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
    # Optional stratigraphic phase/group this measurement belongs to --
    # must match the id of one of the request's top-level `groups` (see
    # BayesianRequest._validate_stratigraphy below).
    group_id: Optional[str] = None
    # The model-tree phase this date belongs to, when the request carries a
    # `model_tree`. Supersedes group_id; see BayesianRequest.
    element_id: Optional[str] = None
    # Prior probability that this date does not belong to the population
    # being modelled -- residual charcoal, intrusive material, lab error.
    # OxCal's `Outlier("General", 0.05)`, and 0.05 is its convention too.
    #
    # Left at 0 (off) rather than defaulted on: it changes reported dates,
    # and turning it on should be the analyst's decision. When set, the
    # date's likelihood becomes a mixture of itself and a heavy-tailed
    # alternative, and the result carries the posterior probability that it
    # was the alternative -- see services/exact_chain.
    #
    # Up to 1.0, not 0.5. A prior of 1.0 is not a mistake or an over-confident
    # guess: it is how OxCal declares material residual OUTRIGHT rather than
    # suspecting it, which is ordinary practice for charcoal known to be old
    # wood -- `Outlier("Charcoal", 1.00)` appears three times in
    # tests/fixtures/oxcal_charcoal. Capping at 0.5 made a published model of
    # that shape impossible to enter at all, which is a worse failure than
    # allowing a strong prior the analyst meant.
    outlier_prior: float = Field(default=0.0, ge=0.0, le=1.0)
    # Which outlier model, when one is applied. "general" is OxCal's
    # symmetric heavy-tailed default, for a date wrong in an unknown way.
    # "charcoal" is one-sided -- the material formed before its context,
    # never after -- for old wood and reworked material. See
    # services/exact_chain._outlier_kernel.
    outlier_model: Literal["general", "charcoal"] = "general"


class SampleGroupInput(BaseModel):
    """
    A named stratigraphic phase/group of samples within a Bayesian
    analysis request. Grouping lets one above/below/equal relationship or
    one TPQ/TAQ limiter apply to every sample in the group at once,
    instead of repeating it per sample pair. Needs a client-supplied id
    (like BayesianMeasurementInput) so relationships and measurements can
    reference it before anything is persisted.
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
    A stratigraphic constraint between two nodes in a Bayesian analysis --
    each side (from/to) can be either a single measurement or a group,
    distinguished by from_type/to_type. Previously an unrecognized
    `relationship` value wasn't rejected -- the constraint builder's if/elif
    chain had no else clause, so a typo (e.g. "beneath" instead of "below") was
    silently dropped with no error and no effect on the model, rather than
    either applying the constraint or failing loudly.
    """
    from_sample_id: str
    to_sample_id: str
    from_type: Literal["sample", "group"] = "sample"
    to_type: Literal["sample", "group"] = "sample"
    relationship: Literal["above", "below", "equal"]


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
    # `gibbs` exists because OxCal is the reference implementation, so an
    # analyst has two reasons to want the same method it uses: reproducing a
    # published analysis, and cross-checking this app's exact solver against an
    # independent route to the same posterior. Both are legitimate, and neither
    # is served by the app insisting it knows better.
    solver: Literal["auto", "exact", "gibbs"] = "auto"
    # How many samples each date's posterior is REPORTED with, not how long
    # anything runs: a chain-shaped model is integrated exactly and has no run
    # length, and services/gibbs.py extends its own run until it converges
    # rather than taking a figure from here on trust.
    mcmc_samples: int = Field(default=4000, ge=100, le=20000)
    # Retained, validated and persisted, but no longer used by either solver --
    # they configured the PyMC/SMC sampler that services/gibbs.py replaced.
    # Kept so a saved analysis's bayesian_config still round-trips through an
    # edit rather than being rejected, and so old rows keep their meaning.
    mcmc_tune: int = Field(default=1000, ge=100, le=20000)
    mcmc_chains: int = Field(default=4, ge=1, le=16)
    # OxCal's UniformSpanPrior, on by default there and here: it stops a
    # sequence's overall span prior depending on how many sub-groups it is
    # divided into (Nicholls and Jones 2001). It is the identity unless the
    # sequence has three or more boundaries. See
    # services/exact_chain._span_prior_correction for what it measurably
    # changes, and services/gibbs.Model._span_priors for how it is grouped
    # when a model has several sequences.
    uniform_span_prior: bool = True


class GroupCalibrationRequest(BaseModel):
    """
    Request body for POST /analysis/calibrate-group: plain (unmodelled)
    calibration of several independent measurements at once, saved as one
    analysis so they land on the Results page's shared-axis plot and
    results table together, in chronological order -- for laying several
    dates out on one timeline without asserting any stratigraphic
    relationship between them. See BayesianRequest for that.

    Reuses BayesianMeasurementInput for its per-measurement fields rather
    than defining a narrower one: group_id/element_id/outlier_prior/
    outlier_model are accepted (so the same measurement objects the UI
    already builds for a Bayesian draft can be sent here unchanged) but
    not applied to anything -- a group calibration has no phases, no
    model tree, and no outlier model to be an outlier from.
    """
    name: str = Field(default="Group Calibration", max_length=200)
    measurements: List[BayesianMeasurementInput] = Field(..., min_length=1)
    project_id: Optional[str] = None
    save: bool = True

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
    # The explicit OxCal-style model structure (nested sequences, phases,
    # boundaries, hiatuses -- see services/model_tree.py). When present it is
    # the authoritative structure and `groups`/`relationships` are ignored;
    # each measurement's element_id says which phase it sits in. When absent,
    # groups/relationships are converted into an equivalent tree server-side,
    # so older clients keep working unchanged.
    model_tree: Optional[Dict[str, Any]] = None

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        v = v.strip()
        return v or "Untitled"

    @model_validator(mode="after")
    def _validate_stratigraphy(self):
        """
        Cross-field checks that need the whole request at once: no
        duplicate client-supplied ids, every group_id/relationship
        endpoint actually resolves to something in this request, and the
        above/below constraints (once groups are expanded to their member
        samples) don't form a stratigraphic cycle -- which would leave no
        set of calendar ages able to satisfy every constraint at once, and
        would otherwise only surface as a confusing MCMC failure/hang
        several minutes into a run.
        """
        measurement_ids = [m.id for m in self.measurements]
        if len(set(measurement_ids)) != len(measurement_ids):
            raise ValueError("measurements contain a duplicate id")

        # An explicit model tree is validated on its own terms -- it has no
        # pairwise relationships to check for cycles (a tree cannot contain
        # one) and its own structural rules instead.
        if self.model_tree is not None:
            from app.services.model_tree import validate_tree
            validate_tree(
                self.model_tree,
                [{'id': m.id, 'sample_name': m.sample_name, 'element_id': m.element_id}
                 for m in self.measurements],
            )
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


# --- Legacy names, kept for anything importing the old schema directly ---

class RadiocarbonMeasurementCreate(MeasurementInput):
    pass


class RadiocarbonMeasurement(RadiocarbonMeasurementCreate):
    id: str
    analysis_id: str
    created_at: datetime

    class Config:
        from_attributes = True
