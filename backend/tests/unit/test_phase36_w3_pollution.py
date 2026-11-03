# backend/tests/unit/test_phase36_w3_pollution.py
"""Phase 36 Worker 3 — W2 stub leak + test-pollution leftovers.

Proves, in an automated and repeatable way:

1. W2 STUB TEARDOWN (success path): importing the W2 test module
   (``test_phase35_w2_auth_controls``) under the Phase 33
   teardown-guarded context leaves NO trace in ``sys.modules`` —
   ``fpdf`` / ``fpdf.enums`` absent again, ``app.main`` evicted —
   while the module's own ``main_module`` reference stays usable
   (the import really happened; the stub was only a transient
   import stand-in). Run in a FRESH interpreter so no other file's
   stub can mask a leak.
2. W2 STUB TEARDOWN (failure path): when ``import app.main`` raises
   a NON-``ImportError`` (sabotaged here with a meta-path finder),
   ``_load_main_module()`` propagates the failure — it is never
   reported as a skip — and the stub is still torn down.
3. PRODUCTION ORDERING: ``FightDetector`` query methods return
   team lists in SORTED order (``sorted(...)`` at
   ``fight_detector.py`` 1349/1426/1451 + the same-class 1487
   ``opponents`` site), identical across ``PYTHONHASHSEED``
   values — a ``list(set)`` order can no longer leak into a
   payload. Proved in fresh interpreters at several hash seeds.
4. BOTH-ORDERS IDENTICAL: the affected subsets
   (W2 + dossier-PDF) give identical outcomes in both collection
   orders. Before this worker, W2-first collected the dossier
   tests via the leaked import-time stub (5 failures) while
   dossier-first died at collection; after the fix both orders
   die identically at dossier collection (fpdf2 absent — the
   dossier tests are unpassable here under any stub, per the
   Phase 35 ledger §9), with no test outcome depending on order.

What this file does NOT prove (stated, not hidden):

* That dossier-PDF generation works. ``fpdf2`` is absent in this
  environment; the dossier tests cannot pass here. The claim is
  order-independence only.
* Suite-wide per-test DB isolation. Assessed and DEFERRED (see the
  Phase 36 W3 report): rebinding the module-global engine /
  ``AsyncSessionLocal`` would have to chase 47 import sites, 15
  test files referencing ``AsyncSessionLocal``, 5 monkeypatch
  sites, and the lifespan/TestClient path — not safely small.
  ``db_session`` (per-test, Phase 35 W5) is unchanged.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

pytestmark = pytest.mark.unit


def _backend_dir() -> str:
    return str(os.path.join(os.path.dirname(__file__), "..", ".."))


def _probe_env() -> dict:
    env = dict(os.environ)
    env["DATABASE_URL"] = "sqlite+aiosqlite:///./test.db"
    env["SECRET_KEY"] = "test-secret-key-for-testing-only"
    env["ENVIRONMENT"] = "development"
    return env


def _run_probe(code: str, timeout: int = 180, extra_env: dict | None = None):
    env = _probe_env()
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=_backend_dir(),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# ── 1. W2 stub teardown, success path (fresh interpreter) ──────────


def test_w2_module_import_leaves_no_stub():
    """Importing the W2 module must leave sys.modules exactly as
    it was: no fpdf stub, no stub-bound app.main."""
    code = (
        "import sys\n"
        "sys.path.insert(0, 'tests/unit')\n"
        "assert 'fpdf' not in sys.modules\n"
        "assert 'app.main' not in sys.modules\n"
        "import importlib.util\n"
        "spec = importlib.util.spec_from_file_location(\n"
        "    'w2mod', 'tests/unit/test_phase35_w2_auth_controls.py')\n"
        "w2 = importlib.util.module_from_spec(spec); spec.loader.exec_module(w2)\n"
        "# The import really happened: the held reference is usable.\n"
        "assert w2.main_module.LOCKOUT_THRESHOLD == 5\n"
        "assert w2.main_module.LOCKOUT_DURATION_SECONDS == 900\n"
        "# ...and the teardown ran: no stub, no stub-bound app.main.\n"
        "assert 'fpdf' not in sys.modules, 'fpdf leak after W2 import'\n"
        "assert 'fpdf.enums' not in sys.modules, 'fpdf.enums leak after W2 import'\n"
        "assert 'app.main' not in sys.modules, 'app.main not evicted after W2 import'\n"
        "# A later bare import fails honestly (no stub to reuse).\n"
        "try:\n"
        "    import app.main\n"
        "except ModuleNotFoundError as exc:\n"
        "    assert 'fpdf' in str(exc)\n"
        "    print('PHASE36_W3_W2_TEARDOWN_OK')\n"
        "else:\n"
        "    raise AssertionError('bare app.main import unexpectedly succeeded')\n"
    )
    proc = _run_probe(code)
    if "PHASE36_W3_W2_TEARDOWN_OK" not in proc.stdout:
        pytest.fail(
            f"W2 teardown proof failed (rc={proc.returncode}): "
            f"{(proc.stdout + proc.stderr)[-1500:]}"
        )


# ── 2. W2 stub teardown, failure path (fresh interpreter) ──────────


def test_w2_load_main_module_non_importerror_propagates_and_tears_down():
    """A non-ImportError importing app.main must propagate (never
    a skip) and the stub must still be torn down."""
    code = (
        "import sys\n"
        "sys.path.insert(0, 'tests/unit')\n"
        "import importlib.util\n"
        "spec = importlib.util.spec_from_file_location(\n"
        "    'w2mod', 'tests/unit/test_phase35_w2_auth_controls.py')\n"
        "w2 = importlib.util.module_from_spec(spec); spec.loader.exec_module(w2)\n"
        "assert 'fpdf' not in sys.modules  # success path already tore down\n"
        "class _BoomFinder:\n"
        "    def find_spec(self, fullname, path=None, target=None):\n"
        "        if fullname == 'app.main':\n"
        "            raise RuntimeError('sabotaged app.main import (W3 probe)')\n"
        "        return None\n"
        "sys.meta_path.insert(0, _BoomFinder())\n"
        "try:\n"
        "    w2._load_main_module()\n"
        "except RuntimeError as exc:\n"
        "    assert 'sabotaged app.main import' in str(exc)\n"
        "    print('PHASE36_W3_W2_FAILURE_PROPAGATED')\n"
        "except Exception as exc:  # noqa: BLE001 - the probe must name it\n"
        "    raise AssertionError(f'wrong exception type: {type(exc).__name__}: {exc}')\n"
        "else:\n"
        "    raise AssertionError('_load_main_module swallowed the RuntimeError')\n"
        "finally:\n"
        "    sys.meta_path = [f for f in sys.meta_path if not isinstance(f, _BoomFinder)]\n"
        "assert 'fpdf' not in sys.modules, 'fpdf leak after failed W2 import'\n"
        "assert 'fpdf.enums' not in sys.modules, 'fpdf.enums leak after failed W2 import'\n"
        "print('PHASE36_W3_W2_FAILURE_TEARDOWN_OK')\n"
    )
    proc = _run_probe(code)
    combined = proc.stdout + proc.stderr
    if "PHASE36_W3_W2_FAILURE_TEARDOWN_OK" not in proc.stdout:
        pytest.fail(
            f"W2 failure-path proof failed (rc={proc.returncode}): "
            f"{combined[-1500:]}"
        )


# ── 3. Production ordering deterministic across hash seeds ──────────

_ORDERING_PROBE = (
    "import json\n"
    "from unittest.mock import AsyncMock\n"
    "from app.agents.base_agent import AgentConfig\n"
    "from app.agents.fight_agents.fight_detector import (\n"
    "    DetectedFight, FightDetector, FightPhase, FightType,\n"
    ")\n"
    "d = FightDetector(config=AgentConfig(agent_id='w3probe', agent_type='fight_detector'), event_bus=AsyncMock())\n"
    "teams = {'team_004', 'team_001', 'team_003', 'team_002', 'team_005'}\n"
    "f1 = DetectedFight(fight_id='f1', fight_type=FightType.MID_RANGE, phase=FightPhase.ENGAGEMENT, teams_involved=set(teams), primary_attacker='team_001', primary_defender='team_002', total_damage=100)\n"
    "f2 = DetectedFight(fight_id='f2', fight_type=FightType.CLOSE_QUARTERS, phase=FightPhase.ENGAGEMENT, teams_involved={'team_003', 'team_001'}, primary_attacker='team_003', primary_defender='team_001')\n"
    "d.detection_state.active_fights['f1'] = f1\n"
    "d.detection_state.active_fights['f2'] = f2\n"
    "out = {\n"
    "    'get_fight': d.get_fight('f1')['teams'],\n"
    "    'get_active_fights': [f['teams'] for f in d.get_active_fights()],\n"
    "    'get_team_fights': [f['opponents'] for f in d.get_team_fights('team_001')],\n"
    "    'resolve_fight': d.resolve_fight('f2')['teams_involved'],\n"
    "}\n"
    "assert out['get_fight'] == sorted(teams), out\n"
    "assert out['resolve_fight'] == ['team_001', 'team_003'], out\n"
    "assert out['get_team_fights'][0] == sorted(teams - {'team_001'}), out\n"
    "print('ORDERING:' + json.dumps(out, sort_keys=True))\n"
)


def test_get_fight_ordering_deterministic_across_hash_seeds():
    """The four sorted query payloads must be byte-identical under
    several PYTHONHASHSEED values (a list(set) order would vary)."""
    outputs = []
    for seed in ("0", "1", "42"):
        proc = _run_probe(
            _ORDERING_PROBE, extra_env={"PYTHONHASHSEED": seed}
        )
        line = next(
            (ln for ln in proc.stdout.splitlines() if ln.startswith("ORDERING:")),
            None,
        )
        if line is None:
            pytest.fail(
                f"ordering probe failed at PYTHONHASHSEED={seed} "
                f"(rc={proc.returncode}): {(proc.stdout + proc.stderr)[-1200:]}"
            )
        outputs.append(line)
    assert outputs[0] == outputs[1] == outputs[2], (
        f"query ordering varies with PYTHONHASHSEED: {outputs}"
    )


# ── 4. Affected subsets identical in both collection orders ────────


def _run_affected_subset(order: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            *order,
            "-o",
            "addopts=",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        cwd=_backend_dir(),
        env=_probe_env(),
        capture_output=True,
        text=True,
        timeout=300,
    )


def _subset_summary(proc: subprocess.CompletedProcess) -> str:
    """The outcome line of a run: exit code + the collection-error
    / summary line. Two runs are 'identical outcomes' when these
    match exactly."""
    tail = (proc.stdout + proc.stderr).strip().splitlines()
    outcome = [ln for ln in tail if "error during collection" in ln or "passed" in ln or "failed" in ln]
    return f"rc={proc.returncode} | " + " / ".join(outcome)


def test_affected_subsets_identical_in_both_collection_orders():
    """W2 + dossier-PDF in both orders must give identical
    outcomes. The dossier file cannot be collected here (fpdf2
    absent); the pre-fix behaviour was W2-first collecting it via
    the leaked stub (5 dossier failures) while dossier-first died
    at collection. Post-fix both orders die identically at
    collection — no test outcome depends on order."""
    w2 = "tests/unit/test_phase35_w2_auth_controls.py"
    pdf = "tests/unit/test_dossier_pdf.py"
    first = _run_affected_subset([w2, pdf])
    second = _run_affected_subset([pdf, w2])
    s1, s2 = _subset_summary(first), _subset_summary(second)
    assert s1 == s2, (
        f"collection-order-dependent outcome:\nW2-first:  {s1}\nPDF-first: {s2}"
    )
    assert first.returncode != 0 and "error during collection" in s1, (
        f"expected both orders to fail identically at dossier "
        f"collection (fpdf2 absent), got: {s1}"
    )
