from sqlalchemy import Column, String, Float, Integer, DateTime, JSON, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base
import uuid

class Project(Base):
    """
    Groups analyses together (by site, dig season, or research question).
    An analysis with project_id = NULL is "unassigned" -- still valid and
    listable, which is also where a deleted project's analyses land rather
    than being deleted with it.
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
    A radiocarbon date in a PROJECT's own collection, independent of any
    analysis: enter a site's dates once, then deploy subsets into as many
    analyses as you like.

    A separate table from RadiocarbonSample because deploying COPIES these
    values, keeping each analysis a self-contained snapshot of what it was
    run on. Editing here never invalidates an already-run analysis; the
    trade is that picking up a corrected date means an explicit re-deploy.
    """
    __tablename__ = "project_samples"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(String, ForeignKey('projects.id'), nullable=False)

    # Same names/semantics as RadiocarbonSample, so deploying is a straight
    # field-for-field copy.
    sample_name = Column(String, nullable=False)
    age_bp = Column(Float, nullable=False)
    error_1sigma = Column(Float, nullable=False)
    material_type = Column(String, nullable=False)
    sample_type = Column(String, nullable=False)  # 'terrestrial' or 'marine'
    curve_id = Column(String, default='intcal20')
    reservoir_age = Column(Float, nullable=True)

    # Local marine reservoir offset and its uncertainty, as on
    # RadiocarbonSample: the deviation from the global average the marine
    # curve already models, not a whole reservoir age. It belongs to the
    # collection because it is a property of where the sample came from, and
    # dates from one site share a value -- hence the bulk edit in the UI.
    delta_r = Column(Float, nullable=True)
    delta_r_error = Column(Float, nullable=True)

    depth_cm = Column(Float, nullable=True)
    location = Column(String, nullable=True)
    notes = Column(String, nullable=True)

    # Defaults pre-filled at deploy time and overridable there.
    # default_context_name is matched by name to build the analysis's
    # SampleGroups, so dates sharing a context name land in the same context.
    default_context_name = Column(String, nullable=True)
    tpq_age_bp = Column(Float, nullable=True)
    taq_age_bp = Column(Float, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    project = relationship('Project', back_populates='samples')


class StoredCalibrationCurve(Base):
    """
    A user-added calibration curve. The three shipped curves stay files on
    disk; services/curve_registry.py serves both kinds behind one interface.

    Two rules keep already-published results valid. A stored curve's NUMBERS
    never change: `source_text` holds the file verbatim and `parse_options`
    records how to read it, and `content_sha256` is checked against the
    re-parsed rows on every load so a later parser change cannot move a curve
    underneath an analysis. Correcting a curve means adding a new one; only
    the editorial fields are mutable. And a curve in use cannot be deleted --
    `archived` hides it from the pickers while leaving it resolvable for a
    rerun (enforced in api/curves.py).
    """
    __tablename__ = "calibration_curves"

    # The slug a date's curve_id points at, chosen at import time. It is the
    # primary key because it is what RadiocarbonSample rows store and what an
    # .oxcal file names.
    id = Column(String, primary_key=True)

    # 'imported' (a file) or 'mixed' (a recipe over two other curves). Builtin
    # curves are not rows here at all.
    kind = Column(String, nullable=False, default='imported')

    # --- editorial: mutable, never cached ---
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    reference = Column(String, nullable=True)
    doi = Column(String, nullable=True)
    # Display grouping, and which sample type this curve suits -- same
    # vocabulary the builtin curves use.
    type = Column(String, nullable=False, default='terrestrial')
    sample_type = Column(String, nullable=False, default='terrestrial')

    # --- the curve itself: immutable ---
    # The uploaded file, exactly as uploaded. Null for a mixed curve, which
    # has no file behind it.
    source_text = Column(String, nullable=True)
    source_filename = Column(String, nullable=True)
    # Column mapping and calendar convention, stored so the parse is
    # reproducible rather than dependent on what the importer would guess.
    parse_options = Column(JSON, nullable=True)
    # For kind='mixed': {a, b, percent_a, percent_a_error}. Stored as a recipe
    # rather than data, so its parents count as in-use while it exists.
    recipe = Column(JSON, nullable=True)
    # sha256 over the normalised numeric rows; see the class docstring.
    content_sha256 = Column(String, nullable=False)

    # Other spellings this curve answers to, so an .oxcal written against
    # 'IntCal13' finds an IntCal13 imported here.
    aliases = Column(JSON, nullable=True)

    archived = Column(Boolean, default=False, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Analysis(Base):
    """Represents a complete radiocarbon analysis"""
    __tablename__ = "analyses"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=False)
    analysis_type = Column(String, nullable=False)  # 'calibration', 'group_calibration', or 'bayesian'
    status = Column(String, default='pending')  # 'pending', 'running', 'completed', 'failed'
    error_message = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # NULL means "unassigned". Runs with save=False are never persisted, so
    # they never reach this table at all.
    project_id = Column(String, ForeignKey('projects.id'), nullable=True)

    # Configuration
    bayesian_config = Column(JSON, nullable=True)  # MCMC parameters if bayesian

    # Which solver produced these results -- exact or MH/Gibbs -- and, when
    # sampled, why the model was not exactly solvable plus the convergence it
    # reached. See api/analysis._run_bayesian for the shape. NULL on a
    # calibration-only analysis.
    solver = Column(JSON, nullable=True)

    # Durations and separations -- functions of SEVERAL modelled ages at once,
    # so they have nowhere to live on a per-date AnalysisResult row. Shape and
    # vocabulary in services/intervals.py. Written only when the run was asked
    # for them (bayesian_config.estimate_durations), so NULL means "not asked
    # for" rather than "nothing to report", which is why the results page
    # offers to re-run instead of showing an empty table. One JSON column
    # rather than a table because nothing queries across these: they are
    # written once per run and read whole.
    intervals = Column(JSON, nullable=True)

    # Relations
    project = relationship('Project', back_populates='analyses')
    # Ordered by the user's arrangement (RadiocarbonSample.sort_order), with
    # created_at and id behind it so the order is total: rows written before
    # sort_order existed all carry 0, and ties between them still have to come
    # back the same way on every read.
    measurements = relationship(
        'RadiocarbonSample',
        back_populates='analysis',
        cascade='all, delete-orphan',
        order_by='(RadiocarbonSample.sort_order, RadiocarbonSample.created_at, RadiocarbonSample.id)',
    )
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
    # Prior probability this date does not belong to the population being
    # modelled (OxCal's Outlier()). Persisted, not just applied: a rerun
    # rebuilds the model from these rows, so leaving it off would silently
    # drop the outlier model.
    outlier_prior = Column(Float, nullable=True)
    # Which outlier model applies when outlier_prior is set: 'general' or
    # 'charcoal'. Persisted for the same reason as the prior beside it.
    outlier_model = Column(String, nullable=True)
    # Superseded by delta_r and never applied to a calibration: it was
    # ambiguous whether it meant a whole reservoir age or a local offset.
    # Kept because existing rows may hold a value.
    reservoir_age = Column(Float, nullable=True)
    depth_cm = Column(Float, nullable=True)
    location = Column(String, nullable=True)
    notes = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Stratigraphic phase/group membership; NULL means this sample stands
    # alone in the Harris matrix. Superseded by element_id below, and retained
    # only so pre-tree analyses can be migrated.
    group_id = Column(String, ForeignKey('sample_groups.id'), nullable=True)

    # The model element (a 'phase') this date belongs to. No FOREIGN KEY: an
    # analysis's elements are rewritten wholesale when its tree is replaced,
    # and a constraint would force a delete/insert ordering for no benefit.
    element_id = Column(String, nullable=True)

    # Where this date sits in the analysis's reading order, as the user
    # arranged it at data entry.
    #
    # Stored rather than left to insertion order, because insertion order is
    # not a thing a relational database promises: a SELECT with no ORDER BY
    # may come back in any order at all, and on Postgres that order really
    # does change after rows are updated. The order is not cosmetic -- it is
    # the order dates appear in within each phase of the results table, in the
    # stacked plot, and in both exports (see model_tree.flatten_structure) --
    # so it needs somewhere to live.
    sort_order = Column(Integer, nullable=False, default=0, server_default='0')

    # Age limiters in cal BP. tpq_age_bp ("terminus post quem") is a MAXIMUM
    # age -- the sample cannot be older than it -- and taq_age_bp ("terminus
    # ante quem") a MINIMUM, so the valid range is [taq_age_bp, tpq_age_bp].
    # See services/exact_chain.py for how these tighten the calendar prior.
    tpq_age_bp = Column(Float, nullable=True)
    taq_age_bp = Column(Float, nullable=True)

    # Relations
    analysis = relationship('Analysis', back_populates='measurements')
    group = relationship('SampleGroup', back_populates='samples')


class ModelElement(Base):
    """
    One node of an analysis's model structure -- the OxCal-style tree of
    Sequences, Phases, Boundaries and Hiatuses that services/model_tree.py
    interprets.

    Stored flat (parent_id + position) rather than as nested JSON so a single
    element can be addressed and dates can reference their phase. The nesting
    is reconstructed on read; `position` orders siblings OLDEST FIRST,
    matching OxCal and the Harris matrix convention.

    Supersedes SampleGroup + StratigraphicRelationship, which survive only so
    older analyses can be migrated on read (model_tree.migrate_from_relationships).
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
    # result (see exact_chain._summarize). Null otherwise.
    posterior = Column(JSON, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    analysis = relationship('Analysis')


class SampleGroup(Base):
    """
    A named stratigraphic phase/group of samples (e.g. "Phase 2 hearth
    fill"), so one above/below/equal relationship or one TPQ/TAQ limiter
    applies to every member at once instead of being repeated per pair.
    """
    __tablename__ = "sample_groups"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    analysis_id = Column(String, ForeignKey('analyses.id'), nullable=False)
    name = Column(String, nullable=False)

    # As on RadiocarbonSample, applied to every member. Where a sample has
    # its own limiter too, the tighter bound wins (stratigraphy.effective_bounds).
    tpq_age_bp = Column(Float, nullable=True)
    taq_age_bp = Column(Float, nullable=True)

    # The MODELLED start/end of this context's span, in OxCal's Boundary
    # sense: its dates are a uniform scatter between the two, so the span may
    # extend beyond the oldest/youngest dated member. Each is a JSON dict with
    # the same shape as a per-sample result, so the frontend renders it with
    # the same component. Null for calibration-only analyses and for a group
    # with fewer than 2 dated members.
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

    # How well this date's MODELLED posterior still fits its UNMODELLED
    # likelihood, as a percentage (Bronk Ramsey 1995; see
    # services/agreement.py). 100 means the model left it alone, above 100
    # that it sharpened it, below about 60 that it is worth re-examining.
    # Null for a plain calibration, where there is no model to disagree with.
    agreement_index = Column(Float, nullable=True)

    # Posterior probability (0-1) that this date does not belong to the
    # population being modelled, when an outlier prior was set on it. Null
    # when no outlier model was applied, which is not the same as zero.
    outlier_probability = Column(Float, nullable=True)

    # Full HPD breakdown as disjoint calendar-age ranges, each with its own
    # probability share -- [{"min": 3300, "max": 3360,
    # "probability_percent": 13.5}, ...]. The hpd_*_min/max columns above are
    # the overall envelope across all of them.
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

    from_sample_id/to_sample_id are polymorphic: each holds a
    RadiocarbonSample.id or a SampleGroup.id, per from_type/to_type. A
    relationship touching a group is expanded into pairwise sample
    constraints at model-build time (services/stratigraphy.py), so editing a
    group's membership does not rewrite every relationship. The polymorphism
    rules out a FOREIGN KEY, so BayesianRequest's validator checks
    referential validity before a run is persisted.
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


class Job(Base):
    """
    One unit of work running outside the request that asked for it.

    A table of its own rather than columns on Analysis: a project rerun spans
    many analyses and a save=False run belongs to none. It also keeps the two
    meanings apart -- `Analysis.status` answers "does this have results", a
    Job answers "is something happening now, how far has it got, can I stop
    it". An analysis whose rerun fails keeps its results and 'completed'
    status while the JOB reads 'failed'.

    Rows are kept after they finish: they are the only record of how long a
    run took and what convergence it reached, and the UI polls them.

    See docs/ASYNC_JOBS_PLAN.md.
    """
    __tablename__ = "jobs"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))

    # 'analysis_run'  -- solve one analysis (a fresh run or a rerun)
    # 'project_rerun' -- rerun every analysis in one project
    kind = Column(String, nullable=False)

    # The analysis or project this is about. Not a ForeignKey: it points at
    # a different table depending on `kind`. Indexed because the results page
    # looks a job up by its analysis.
    target_id = Column(String, nullable=True, index=True)

    # 'queued' -> 'running' -> 'completed' | 'failed' | 'cancelled'.
    # A job cancelled before it starts goes straight from queued to
    # cancelled without ever running.
    state = Column(String, nullable=False, default='queued')

    # 0..1, an UPPER BOUND rather than a prediction: the sampler stops as soon
    # as it converges, so a run finishing at 0.3 succeeded early rather than
    # being cut short -- which is why the UI shows convergence beside the bar.
    progress = Column(Float, nullable=True)

    # Whatever the running job wants to say about itself, by kind:
    #   analysis_run   {'phase', 'sweeps', 'max_sweeps', 'convergence'}
    #   project_rerun  {'completed', 'total', 'failed': [{id, name, error}]}
    # Free-form: read by the progress display and nothing that depends on it.
    detail = Column(JSON, nullable=True)

    error_message = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
