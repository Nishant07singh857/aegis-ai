"""AEGIS AI — Team dossiers, fight log & PDF reports (Phase 11, Worker W2).

Team-intel analytics over real fight/kill/rotation event data, plus
PDF report generation (fpdf2). Phase 32 (K-H2): reads are NOT public —
every route here (reads and ingest alike) requires operator-or-admin
auth; see router.py.

Nothing here fabricates statistics: every computed value carries its
evidence, and thin data is reported honestly as "insufficient data".
"""

from .router import router  # noqa: F401
from .service import DossierService, FightService, ReportService  # noqa: F401
