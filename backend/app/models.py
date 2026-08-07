from sqlalchemy import Column, String, Float, Integer, DateTime, JSON, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base
import uuid

class Project(Base):
    """
    Groups analyses together (e.g. by site, dig season, or research
    question) so the dashboard doesn't have to list every analysis ever
    run. An analysis with project_id = NULL is "unassigned" -- still
    valid and listable, just not filed under any project (this is also
    where a project's analyses land if the project itself is deleted,
    rather than deleting the analyses along with it).
    """
    __tablename__ = "projects"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    analyses = relationship('Analysis', back_populates='project')
    samples = relationship('ProjectSample', back_populates='project', cascade='all, delete-orphan')


class ProjectSample(Base):
    """
    A radiocarbon date held in a PROJECT's own date collection, independent
    of any analysis -- enter a site's dates once here, then deploy subsets
    of them into as many analyses as you like, each modelling them
    differently (different contexts, different stratigraphy).

    Deliberately a separate table from RadiocarbonSample rather than
    RadiocarbonSample gaining a nullable analysis_id: deploying COPIES
    these values into an analysis's own RadiocarbonSample rows, so an
    analysis is always a self-contained snapshot of exactly what it was
    run on. Editing an entry here therefore never mutates an already-run
    analysis or silently invalidates its stored results -- important for
    reproducibility, since a saved result must keep matching the inputs
    displayed alongside it. The cost is that picking up a corrected date
    in an existing analysis is an explicit action (re-deploy, or edit that
    analysis), which is the intended trade.
    """
    __tablename__ = "project_samples"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(String, ForeignKey('projects.id'), nullable=False)

    # Measurement fields -- same names/semantics as RadiocarbonSample, so
    # deploying is a straight field-for-field copy.
    sample_name = Column(String, nullable=False)
    age_bp = Column(Float, nullable=False)
    error_1sigma = Column(Float, nullable=False)
    material_type = Column(String, nullable=False)
    sample_type = Column(String, nullable=False)  # 'terrestrial' or 'marine'
    curve_id = Column(String, default='intcal20')
    reservoir_age = Column(Float, nullable=True)
    depth_cm = Column(Float, nullable=True)
    location = Column(String, nullable=True)
    notes = Column(String, nullable=True)

    # Archaeological defaults, pre-filled when this date is deployed into an
    # analysis and freely overridable there. default_context_name is matched
    # by name at deploy time to build that analysis's SampleGroups, so dates
    # sharing a context name land in the same context without re-grouping
    # them by hand every time.
    default_context_name = Column(String, nullable=True)
    tpq_age_bp = Column(Float, nullable=True)
    taq_age_bp = Column(Float, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    project = relationship('Project', back_populates='samples')


class Analysis(Base):
    """Represents a complete radiocarbon analysis"""
    __tablename__ = "analyses"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=False)
    analysis_type = Column(String, nullable=False)  # 'calibration' or 'bayesian'
    status = Column(String, default='pending')  # 'pending', 'running', 'completed', 'failed'
    error_message = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Optional project grouping -- NULL means "unassigned". Analyses run
    # with save=False (quick test runs) are never persisted at all, so
    # they never reach this table in the first place.
    project_id = Column(String, ForeignKey('projects.id'), nullable=True)

    # Configuration
    bayesian_config = Column(JSON, nullable=True)  # MCMC parameters if bayesian

    # Relations
    project = relationship('Project', back_populates='analyses')
    measurements = relationship('RadiocarbonSample', back_populates='analysis', cascade='all, delete-orphan')
    results = relationship('AnalysisResult', back_populates='analysis', cascade='all, delete-orphan')

class RadiocarbonSample(Base):
    """Represents a radiocarbon measurement"""
    __tablename__ = "radiocarbon_samples"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    analysis_id = Column(String, ForeignKey('analyses.id'), nullable=False)
    sample_name = Column(String, nullable=False)
    age_bp = Column(Float, nullable=False)  # Radiocarbon age in BP
    error_1sigma = Column(Float, nullable=False)
    material_type = Column(String, nullable=False)  # e.g., 'bone', 'wood', 'charcoal'
    sample_type = Column(String, nullable=False)  # 'terrestrial' or 'marine'
    curve_id = Column(String, default='intcal20')
    # Local marine reservoir offset (Delta-R) and its 1-sigma uncertainty,
    # in radiocarbon years. A marine curve already models the global average
    # reservoir effect, so only the local deviation from it belongs here --
    # see schemas.MeasurementInput.delta_r for the sign convention.
    delta_r = Column(Float, nullable=True)
    delta_r_error = Column(Float, nullable=True)
    # Superseded by delta_r. Kept because existing rows may hold a value,
    # but never applied to a calibration -- it was ambiguous whether it
    # meant a whole reservoir age or a local offset, and starting to apply
    # it would have silently changed already-published results.
    # Prior probability this date does not belong to the population being
    # modelled (OxCal's Outlier()). Persisted, not just applied: a rerun
    # rebuilds the model from these rows, so leaving it off would silently
    # drop the outlier model on every rerun -- the same trap delta_r above
    # is commented against.
    outlier_prior = Column(Float, nullable=True)
    # Which outlier model applies when outlier_prior is set: 'general' or
    # 'charcoal'. Persisted for the same reason as the prior beside it.
    outlier_model = Column(String, nullable=True)
    reservoir_age = Column(Float, nullable=True)  # For marine samples
    depth_cm = Column(Float, nullable=True)
    location = Column(String, nullable=True)
    notes = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Optional stratigraphic phase/group membership -- NULL means this
    # sample stands alone (not part of any group) in the Harris matrix.
    # SUPERSEDED by element_id below; retained so analyses saved before the
    # model tree existed can still be migrated.
    group_id = Column(String, ForeignKey('sample_groups.id'), nullable=True)

    # The model element (a 'phase') this date belongs to -- the replacement
    # for group_id. No FOREIGN KEY: an analysis's elements are rewritten
    # wholesale when its tree is replaced, and a DB-level constraint would
    # force a specific delete/insert ordering for no real benefit, the same
    # reasoning StratigraphicRelationship's polymorphic columns use.
    element_id = Column(String, nullable=True)

    # Optional age limiters (both expressed directly in cal BP, matching
    # every other age field in this app): tpq_age_bp is a *maximum* age
    # bound -- "terminus post quem", the sample cannot be OLDER than this
    # (it postdates the terminus) -- and taq_age_bp is a *minimum* age
    # bound -- "terminus ante quem", the sample cannot be YOUNGER than this
    # (it predates the terminus). Valid range is [taq_age_bp, tpq_age_bp];
    # see schemas/sample.py's cross-field validator for the sanity check on
    # that ordering, and services/bayesian.py for how these tighten each
    # sample's calendar-age prior.
    tpq_age_bp = Column(Float, nullable=True)
    taq_age_bp = Column(Float, nullable=True)

    # Relations
    analysis = relationship('Analysis', back_populates='measurements')
    group = relationship('SampleGroup', back_populates='samples')


class ModelElement(Base):
    """
    One node of an analysis's explicit model structure -- the OxCal-style
    tree of Sequences, Phases, Boundaries and Hiatuses that
    services/model_tree.py interprets.

    Stored flat (parent_id + position) rather than as nested JSON so a
    single element can be addressed, and so the dates that live in a phase
    can reference it by foreign key. The nesting is reconstructed on read;
    `position` orders siblings OLDEST FIRST, matching OxCal's reading order
    and the Harris matrix convention of oldest at the bottom.

    This supersedes SampleGroup + StratigraphicRelationship as the source of
    stratigraphic structure. Those tables are still present so analyses
    saved before this existed can be migrated on read (see
    model_tree.migrate_from_relationships); nothing writes new structure to
    them.
    """
    __tablename__ = "model_elements"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    analysis_id = Column(String, ForeignKey('analyses.id'), nullable=False)
    # NULL for the single root element of an analysis.
    parent_id = Column(String, nullable=True)
    position = Column(Integer, nullable=False, default=0)

    element_type = Column(String, nullable=False)  # sequence | phase | boundary | hiatus
    name = Column(String, nullable=True)

    # Age limiters, as on a sample; an element's limiter applies to
    # everything nested inside it, tightest winning.
    tpq_age_bp = Column(Float, nullable=True)
    taq_age_bp = Column(Float, nullable=True)

    # For a boundary: its posterior after a run, same shape as a per-sample
    # result (see BayesianChronology._summarize_posterior). Null for every
    # other element type, and until the analysis has been run.
    posterior = Column(JSON, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    analysis = relationship('Analysis')


class SampleGroup(Base):
    """
    A named stratigraphic phase/group of samples (e.g. "Phase 2 hearth
    fill"). Grouping lets a user define a single above/below/equal
    relationship or a single TPQ/TAQ limiter that applies to every sample
    in the group at once, instead of repeating it per sample pair --
    the same "boundary/phase" idea OxCal uses, simplified to apply
    directly to each member sample's age prior rather than a separate
    phase-boundary latent variable.
    """
    __tablename__ = "sample_groups"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    analysis_id = Column(String, ForeignKey('analyses.id'), nullable=False)
    name = Column(String, nullable=False)

    # Same semantics as RadiocarbonSample.tpq_age_bp/taq_age_bp above, just
    # applied to every sample in the group. If a sample has its own limiter
    # AND belongs to a group with one, the tighter of the two bounds wins
    # (see services/bayesian.py).
    tpq_age_bp = Column(Float, nullable=True)
    taq_age_bp = Column(Float, nullable=True)

    # The MODELLED start/end of this context's own span, filled in after a
    # Bayesian run that includes this group -- Boundary parameters in the
    # same sense as OxCal's Boundary command: the context's dates are
    # modelled as a random uniform scatter between these two, so they may
    # extend beyond the oldest/youngest dated member rather than just
    # reproducing it (see BayesianChronology.build_model/._sample_boundaries
    # in services/bayesian.py for the generative model and how these are
    # recovered). Each is a JSON dict with the same shape as a per-sample result
    # (posterior_mean/median/std/hpd_68/hpd_95/hpd_68_ranges/hpd_95_ranges/
    # probability_distribution), so the frontend can render it with the
    # exact same component used for a sample's age. Left null for
    # calibration-only analyses, and for a group with fewer than 2 dated
    # members (a span isn't a meaningful concept for 0 or 1 dates).
    modeled_start = Column(JSON, nullable=True)
    modeled_end = Column(JSON, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    analysis = relationship('Analysis')
    samples = relationship('RadiocarbonSample', back_populates='group')

class AnalysisResult(Base):
    """Stores results of an analysis"""
    __tablename__ = "analysis_results"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    analysis_id = Column(String, ForeignKey('analyses.id'), nullable=False)
    sample_id = Column(String, ForeignKey('radiocarbon_samples.id'), nullable=True)
    
    # Results
    calibrated_age_bp = Column(Float, nullable=True)
    posterior_mean = Column(Float, nullable=True)
    posterior_median = Column(Float, nullable=True)
    posterior_std = Column(Float, nullable=True)
    hpd_68_min = Column(Float, nullable=True)
    hpd_68_max = Column(Float, nullable=True)
    hpd_95_min = Column(Float, nullable=True)
    hpd_95_max = Column(Float, nullable=True)

    # How well this date's MODELLED posterior still fits its own UNMODELLED
    # likelihood, as a percentage (Bronk Ramsey 1995; see
    # services/agreement.py). 100 means the model left it alone, above 100
    # that the model sharpened it onto its own peak, and below about 60 that
    # the model had to move it far enough to be worth re-examining. Null for
    # results computed before this existed, and for a plain calibration,
    # where there is no model to disagree with.
    agreement_index = Column(Float, nullable=True)

    # Posterior probability (0-1) that this date does not belong to the
    # population being modelled, when an outlier prior was set on it. Null
    # when no outlier model was applied, which is not the same as zero.
    outlier_probability = Column(Float, nullable=True)

    # Full HPD breakdown as one or more disjoint calendar-age ranges, each
    # with its own probability share -- e.g.
    # [{"min": 3300, "max": 3360, "probability_percent": 13.5}, ...].
    # hpd_68_min/max and hpd_95_min/max above remain the overall envelope
    # across all such ranges (kept for simple/legacy consumers); these
    # columns hold the full multi-range detail for callers that need it,
    # per standard OxCal/CALIB-style calibration reporting.
    hpd_68_ranges = Column(JSON, nullable=True)
    hpd_95_ranges = Column(JSON, nullable=True)

    # Posterior samples for visualization (stored as JSON array)
    posterior_samples = Column(JSON, nullable=True)
    probability_distribution = Column(JSON, nullable=True)  # [cal_age, prob] pairs
    
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relations
    analysis = relationship('Analysis', back_populates='results')

class StratigraphicRelationship(Base):
    """
    Stores stratigraphic relationships for Bayesian constraints.

    from_sample_id/to_sample_id are polymorphic: each one holds either a
    RadiocarbonSample.id or a SampleGroup.id, distinguished by from_type/
    to_type ('sample' or 'group'). A group-to-group or group-to-sample
    relationship is expanded into the full set of pairwise sample
    constraints at model-build time (see services/stratigraphy.py) rather
    than stored expanded, so editing a group's membership doesn't require
    rewriting every relationship that touches it. Because of this
    polymorphism there's no database-level FOREIGN KEY on these two
    columns (a plain FK could only ever point at one table); referential
    validity is instead checked by BayesianRequest's validator before an
    analysis is ever run or persisted.
    """
    __tablename__ = "stratigraphic_relationships"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    analysis_id = Column(String, ForeignKey('analyses.id'), nullable=False)
    from_sample_id = Column(String, nullable=False)
    to_sample_id = Column(String, nullable=False)
    from_type = Column(String, nullable=False, default='sample')  # 'sample' or 'group'
    to_type = Column(String, nullable=False, default='sample')  # 'sample' or 'group'
    relationship = Column(String, nullable=False)  # 'above', 'below', or 'equal'
    created_at = Column(DateTime, default=datetime.utcnow)
