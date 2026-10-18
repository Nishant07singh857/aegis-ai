"""AEGIS AI — Team dossiers, fight log & PDF reports (Phase 11, Worker W2).

Read-only analytics over real fight/kill/rotation event data, plus an
operator-gated fight ingest endpoint and PDF report generation (fpdf2).

Nothing here fabricates statistics: every computed value carries its
evidence, and thin data is reported honestly as "insufficient data".
"""

from .router import router  # noqa: F401
from .service import DossierService, FightService, ReportService  # noqa: F401
