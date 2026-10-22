# backend/app/services/director_learning/__init__.py
"""Director Learning Loop package (Phase 27).

W1 foundation: the decision journal (every LiveDirector decision
persisted), human-feedback capture (semi_auto suggestion fates,
including the observed ``modified`` fate), and manual-mode shadow
reconciliation (human vs AI-shadow divergences). W2 (labeler), W3
(learning engine) and W4 (UI/API) build on the tables in ``models.py``
and the read helpers in ``queries.py``.

With nothing registered, every hook is a no-op and director behaviour
is unchanged: ``LiveDirector._log_decision`` and the DirectorService
suggestion flows only forward to sinks when :func:`build_director_learning`
has registered them (lifespan, failure-isolated).
"""

from __future__ import annotations

from . import models  # noqa: F401  (registers the ORM tables)
from .journal import (  # noqa: F401
    DecisionJournal,
    build_director_learning,
    clear_sinks,
    get_journal,
    notify_decision,
    notify_fate,
    register_decision_sink,
    register_fate_sink,
    set_journal,
)
from .models import (  # noqa: F401
    LEARNING_STATE_ID,
    DirectorDecision,
    DirectorDecisionLabel,
    DirectorLearningAdjustment,
    DirectorLearningState,
    DirectorShadowDivergence,
    DirectorSuggestionFate,
)
from .shadow import (  # noqa: F401
    ShadowReconciler,
    classify_write,
    full_auto_margin_required,
    shadow_would_switch,
)
from .knobs import (  # noqa: F401
    KNOB_DEFAULTS,
    KNOB_REGISTRY,
    ForbiddenKnobError,
    KnobProvider,
    KnobSpec,
    clear_provider,
    get_provider,
    register_provider,
)
from .engine import (  # noqa: F401
    LearningEngine,
    get_engine,
    get_learning_mode,
    set_engine,
    set_learning_mode,
)
