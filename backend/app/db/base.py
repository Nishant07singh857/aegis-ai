# backend/app/db/base.py
"""
AEGIS AI - Database Foundation
Async SQLAlchemy engine, session management, base model
Production-grade with connection pooling, retry logic, health checks
"""

import asyncio
import time
import logging
from typing import AsyncGenerator, Optional, Any, Dict, NoReturn
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncSession,
    async_sessionmaker,
    AsyncEngine,
)
from sqlalchemy.orm import DeclarativeBase, declared_attr
from sqlalchemy import (
    Column, DateTime, Boolean, func, text, MetaData,
)
from sqlalchemy.exc import SQLAlchemyError, OperationalError, DisconnectionError, DBAPIError
from sqlalchemy import event as sa_event

from ..config import settings
from .error_scrub import (
    is_credential_key,
    scrub_db_error_text,
    scrubbed_exception,
)

logger = logging.getLogger("aegis.db")


def _db_secrets() -> list:
    """Known secret values for verbatim redaction (Phase 36, W1 / N-1).

    The configured DATABASE_URL itself, so a driver message echoing
    it in a shape no scrubber pattern recognises is still redacted.
    The scrubber ignores blank/short entries, so an unconfigured URL
    is safe to pass.
    """
    return [settings.DATABASE_URL or ""]


def _scrubbed_exc_info(exc: BaseException):
    """``exc_info`` tuple whose formatted traceback prints ONLY the
    scrubbed message (Phase 36, W1 / N-1).

    ``exc_info=True`` would print the ORIGINAL exception's message as
    the traceback's final line, so scrubbing only the log-message
    f-string is not enough — the secret would still reach the log
    via the traceback. This keeps the original frames (traceback
    source lines never carry values) but substitutes the scrubbed
    surrogate as the exception value, so the traceback's final line
    is scrubbed too. (The printed type name becomes the surrogate's
    plain ``Exception``; the log call's own text still names the site.)
    """
    return (type(exc), scrubbed_exception(exc, secrets=_db_secrets()), exc.__traceback__)


def _scrub_params_value(value: Any, *, _depth: int = 0) -> Any:
    """Recursively scrub string leaves of a DBAPI ``params`` value.

    Bind parameters are application data, not credentials, but a
    parameter value could still be (or contain) the configured URL
    string — scrub the strings, keep every other value untouched.
    Depth-capped so a pathological nested value cannot recurse
    forever; beyond the cap the value is replaced by its scrubbed
    string form.

    Phase 37 W1-fix (QA D2/D3):
    * D2 — a non-``str`` leaf is kept ONLY when its string form
      survives scrubbing unchanged (``_scrub_arg``'s rule, below):
      SQLAlchemy renders ``params`` via ``repr()`` in ``str(exc)``,
      so an opaque object whose ``str()`` echoes the URL would
      otherwise leak into the surrogate's own ``str()``. Clean
      values keep their exact type and identity.
    * D3 — a dict value under a credential-named key
      (``is_credential_key`` — same pattern family as
      ``_CREDENTIAL_KV_RE`` in ``error_scrub``) is redacted
      wholesale: the key IS the context the leaf scrubber
      previously discarded.

    Phase 37 W1-fix2 (QA re-gate D1r/D2r):
    * D1r — the D2 rule checked only ``str()``, but SQLAlchemy
      renders via ``repr()``: an object with a clean ``__str__``
      and a secret-bearing ``__repr__`` was kept untouched and
      leaked. A non-``str`` leaf is now kept ONLY when BOTH its
      ``str()`` and its ``repr()`` survive scrubbing unchanged
      (see :func:`_scrub_opaque_leaf`).
    * D2r — the depth cap stringified the value, and a stringified
      dict ``{'pwd': '<password>'}`` matches no scrub pattern, so
      a credential-keyed value nested >= 5 deep leaked the bare
      password. At the cap, credential-keyed values are now
      redacted first (see :func:`_redact_credential_keys_deep`),
      so the "redacted at any nesting depth" claim actually holds.
    """
    if isinstance(value, str):
        return scrub_db_error_text(value, secrets=_db_secrets())
    if _depth >= 4:
        try:
            # D2r: redact credential-keyed values at ANY depth
            # before stringifying — the stringified dict
            # ``{'pwd': '<password>'}`` matches no scrub pattern,
            # so the bare password leaked at depth >= 5. The
            # redaction is iterative (explicit stack), so the cap's
            # stack-exhaustion protection is not re-opened.
            return scrub_db_error_text(
                str(_redact_credential_keys_deep(value)),
                secrets=_db_secrets(),
            )
        except Exception:  # noqa: BLE001 - stringification itself failed
            return "<redacted db error>"
    if isinstance(value, dict):
        return {
            _scrub_params_value(k, _depth=_depth + 1): (
                # "<redacted>" mirrors error_scrub._REDACTED.
                "<redacted>"
                if is_credential_key(k)
                else _scrub_params_value(v, _depth=_depth + 1)
            )
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        scrubbed = [_scrub_params_value(v, _depth=_depth + 1) for v in value]
        return tuple(scrubbed) if isinstance(value, tuple) else scrubbed
    if isinstance(value, (set, frozenset)):
        return type(value)(
            _scrub_params_value(v, _depth=_depth + 1) for v in value
        )
    # D2/D1r: opaque leaf — _scrub_opaque_leaf's rule (BOTH str()
    # and repr() must survive scrubbing unchanged; see the helper).
    return _scrub_opaque_leaf(value)


def _scrub_opaque_leaf(value: Any) -> Any:
    """Keep-or-replace rule for a non-``str`` bind-param / ``args`` leaf.

    Shared by :func:`_scrub_params_value` and :func:`_scrub_arg` so
    both channels enforce the same rule. SQLAlchemy renders
    ``params`` / ``args`` via ``repr()`` in ``str(exc)``, so BOTH
    ``str()`` and ``repr()`` must survive scrubbing unchanged for
    the value to be kept — type and identity preserved. Otherwise
    the value is replaced by its scrubbed ``str()`` form: an object
    with a clean ``__str__`` but a secret-bearing ``__repr__``
    (Phase 37 re-gate D1r) is replaced, not kept. A ``repr()`` that
    raises is treated as unprovable-clean and fails closed to the
    scrubbed string; a ``str()`` that raises fails closed to
    ``"<redacted db error>"``.
    """
    try:
        text = str(value)
    except Exception:  # noqa: BLE001 - stringification itself failed
        return "<redacted db error>"
    scrubbed_text = scrub_db_error_text(text, secrets=_db_secrets())
    try:
        rep = repr(value)
    except Exception:  # noqa: BLE001 - repr itself failed; unprovable-clean
        return scrubbed_text
    if scrubbed_text == text and (
        scrub_db_error_text(rep, secrets=_db_secrets()) == rep
    ):
        return value
    return scrubbed_text


def _redact_credential_keys_deep(value: Any) -> Any:
    """Deep copy of *value* with every dict value under a credential
    key replaced by ``"<redacted>"`` — at ANY nesting depth.

    Used only at the depth cap of :func:`_scrub_params_value`, where
    the value is about to be stringified: the stringified dict
    ``{'pwd': '<password>'}`` matches no scrub pattern, so the bare
    password is the one shape the text scrubber cannot recognise —
    it is redacted here, structurally, before stringification.
    Iterative (explicit stack, no recursion), so the cap's
    stack-exhaustion protection is not re-opened; cyclic containers
    are cut with ``"<redacted>"``. Non-container leaves pass through
    untouched (the caller stringifies + text-scrubs the result).
    Dict keys pass through untouched: a key can never contain a dict
    (unhashable), so no credential-keyed value can hide inside one.
    """
    if not isinstance(value, (dict, list, tuple, set, frozenset)):
        return value
    rebuilt: dict[int, Any] = {}
    active: set[int] = set()
    stack: list[tuple[Any, bool]] = [(value, False)]
    while stack:
        node, processed = stack.pop()
        if processed:
            active.discard(id(node))
            if isinstance(node, dict):
                rebuilt[id(node)] = {
                    k: (
                        "<redacted>"
                        if is_credential_key(k)
                        else rebuilt.get(id(v), v)
                    )
                    for k, v in node.items()
                }
            elif isinstance(node, (list, tuple)):
                items = [rebuilt.get(id(v), v) for v in node]
                rebuilt[id(node)] = (
                    tuple(items) if isinstance(node, tuple) else items
                )
            else:  # set / frozenset
                rebuilt[id(node)] = type(node)(
                    rebuilt.get(id(v), v) for v in node
                )
            continue
        if id(node) in active:
            # Cycle back to an ancestor still being rebuilt: cut it.
            # The ancestor's own (genuine) rebuild overwrites this
            # placeholder later; the cycle-edge consumer above has
            # already read "<redacted>" by then.
            rebuilt[id(node)] = "<redacted>"
            continue
        active.add(id(node))
        stack.append((node, True))
        children = node.values() if isinstance(node, dict) else node
        for child in children:
            if isinstance(child, (dict, list, tuple, set, frozenset)):
                stack.append((child, False))
    return rebuilt[id(value)]


def _scrub_arg(arg: Any) -> Any:
    """Scrub one exception ``args`` element for the surrogate rebuild.

    Strings are scrubbed; nested exceptions become scrubbed plain
    surrogates (never chained to the raw original); containers are
    scrubbed leaf-wise; any other value is kept ONLY when BOTH its
    ``str()`` and its ``repr()`` survive scrubbing unchanged,
    otherwise it is replaced by the scrubbed string — so no secret
    can hide behind ``str()`` / ``repr()`` of an opaque object
    (e.g. a DBAPI connection object whose repr echoes the URL).
    (Phase 37 re-gate D1r: the earlier version of this docstring
    claimed repr safety while the code checked ``str()`` only —
    both are now checked, via :func:`_scrub_opaque_leaf`.)
    """
    if isinstance(arg, str):
        return scrub_db_error_text(arg, secrets=_db_secrets())
    if isinstance(arg, BaseException):
        return scrubbed_exception(arg, secrets=_db_secrets())
    if isinstance(arg, (dict, list, tuple, set, frozenset)):
        return _scrub_params_value(arg)
    return _scrub_opaque_leaf(arg)


def _raise_clean_surrogate(surrogate: BaseException) -> NoReturn:
    """Raise *surrogate* with ``__context__`` guaranteed ``None``.

    Raising after the ``except`` block exits is NOT enough on its
    own (Phase 37 W1-fix, QA D1 — probed, not assumed): when the
    raw exception is thrown INTO an async generator via ``athrow``
    during unwinding — exactly how ``@asynccontextmanager`` drives
    ``get_db_context``, and how FastAPI drives ``get_db``'s
    endpoint-error path — ``sys.exception()`` is STILL the raw
    exception at the raise point after the block, so a plain
    ``raise`` re-attaches it as ``__context__`` (``from None``
    only clears ``__cause__``; pre-clearing ``__context__`` before
    the raise does not survive it either). The only shape that
    holds in every case: raise, catch the surrogate itself, clear
    its ``__context__``, and BARE re-raise — a bare re-raise does
    not touch ``__context__``, so the cleared value survives the
    propagation out of this helper, out of the generator, and out
    of ``__aexit__``. ``__cause__`` is already ``None`` (the
    surrogate is built fresh, never chained) and is left alone.
    """
    try:
        raise surrogate
    except BaseException:  # noqa: BLE001 - must catch the surrogate
        # itself whatever its class (incl. BaseException subclasses).
        surrogate.__context__ = None
        raise


def _scrubbed_surrogate(exc: BaseException) -> BaseException:
    """Scrubbed surrogate of *exc*, for re-raising (Phase 37, W1 / N-1).

    Phase 36 scrubbed every DB log line but ``get_db`` /
    ``get_db_context`` / ``init_db`` still bare-raised the ORIGINAL
    exception, so any caller (FastAPI, the lifespan, a service) could
    receive — and log, or render into a 500 — a driver message
    embedding the DATABASE_URL. This is the raise-site half of the
    guarantee: the surrogate's message, ``str()``, ``args`` and
    ``__cause__``/``__context__`` chain can carry no credential —
    callers raise it via :func:`_raise_clean_surrogate` (compute in
    the ``except`` block, raise after it exits; the helper clears
    ``__context__`` on a bare re-raise, which is what makes the
    guarantee hold even when the raw exception is thrown in via
    ``athrow`` during unwinding — see the helper). The TYPE is
    preserved wherever SQLAlchemy's constructor shape allows it:

    * ``DBAPIError`` subclasses (``OperationalError``,
      ``IntegrityError``, ...) carry structured ``(statement,
      params, orig)`` — the surrogate is rebuilt from the scrubbed
      statement, leaf-scrubbed params and a scrubbed ``orig``
      surrogate, with ``hide_parameters`` carried over. (Probed on
      SQLAlchemy 2.1.5: ``exc.args`` is a single formatted string,
      NOT ``(statement, params, orig)``, so args-based rebuild
      would misplace the values — the structured attributes are the
      only correct source.)
    * every other ``SQLAlchemyError`` — including
      ``DisconnectionError``, which in SQLAlchemy 2.x extends
      ``SQLAlchemyError`` directly, has no ``statement`` /
      ``params`` / ``orig`` attributes and takes no keyword
      arguments — is rebuilt as ``type(exc)(*scrubbed_args)``.
    * anything else (non-SQLAlchemy) collapses to the plain
      scrubbed ``Exception`` from ``error_scrub``.

    Any rebuild failure (unknown constructor shape, un-scrubbable
    arg) falls back to the plain scrubbed ``Exception``: the
    guarantee is "never a raw credential", not "always the same
    type". The original exception is never mutated, so the already-
    scrubbed log line keeps the full traceback for debugging.
    """
    if isinstance(exc, DBAPIError) and hasattr(exc, "statement"):
        try:
            statement = exc.statement
            if isinstance(statement, str):
                statement = scrub_db_error_text(
                    statement, secrets=_db_secrets()
                )
            orig = exc.orig
            if isinstance(orig, BaseException):
                orig = scrubbed_exception(orig, secrets=_db_secrets())
            elif orig is not None:
                orig = Exception(
                    scrub_db_error_text(str(orig), secrets=_db_secrets())
                )
            kwargs: Dict[str, Any] = {}
            if bool(getattr(exc, "hide_parameters", False)):
                kwargs["hide_parameters"] = True
            return type(exc)(
                statement,
                _scrub_params_value(exc.params),
                orig,
                **kwargs,
            )
        except Exception:  # noqa: BLE001 - rebuild failed; fall back
            return scrubbed_exception(exc, secrets=_db_secrets())
    if isinstance(exc, SQLAlchemyError):
        try:
            return type(exc)(*(_scrub_arg(a) for a in exc.args))
        except Exception:  # noqa: BLE001 - rebuild failed; fall back
            return scrubbed_exception(exc, secrets=_db_secrets())
    return scrubbed_exception(exc, secrets=_db_secrets())

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# NAMING CONVENTIONS FOR CONSTRAINTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Base(DeclarativeBase):
    """
    Base model class with common columns and utilities.
    All database models inherit from this.
    """
    
    metadata = metadata
    
    @declared_attr
    def __tablename__(cls) -> str:
        """Auto-generate table name from class name"""
        import re
        name = cls.__name__
        # Convert CamelCase to snake_case
        name = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
        name = re.sub('([a-z0-9])([A-Z])', r'\1_\2', name).lower()
        return name
    
    # Common columns for ALL models
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="Record creation timestamp"
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        comment="Record last update timestamp"
    )
    is_deleted = Column(
        Boolean,
        default=False,
        nullable=False,
        comment="Soft delete flag"
    )
    deleted_at = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="Soft delete timestamp"
    )
    
    def to_dict(self, exclude: Optional[set] = None) -> Dict[str, Any]:
        """Convert model instance to dictionary"""
        exclude = exclude or {"_sa_instance_state"}
        result = {}
        for column in self.__table__.columns:
            if column.name not in exclude:
                value = getattr(self, column.name)
                # Handle datetime serialization
                if hasattr(value, 'isoformat'):
                    value = value.isoformat()
                result[column.name] = value
        return result
    
    def update(self, **kwargs) -> None:
        """Update model attributes"""
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
    
    def soft_delete(self) -> None:
        """Soft delete the record"""
        self.is_deleted = True
        self.deleted_at = func.now()
    
    def __repr__(self) -> str:
        """String representation"""
        attrs = []
        for col in self.__table__.columns:
            if col.primary_key:
                attrs.append(f"{col.name}={getattr(self, col.name)}")
        return f"<{self.__class__.__name__}({', '.join(attrs)})>"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ASYNC ENGINE FACTORY
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _register_engine_listeners(engine: AsyncEngine) -> None:
    """Shared connect/checkout/close listeners for every engine this
    module creates (real DB and the Phase 33 no-DB placeholder alike).
    Extracted so the placeholder branch does not duplicate them; the
    listeners only log, they never change connection behaviour.
    """
    # StaticPool (the no-DB placeholder) has no pool.size()/checkedin()
    # in the same way; guard the checkout log so the placeholder can
    # never crash on a debug log line.
    def _pool_size() -> str:
        try:
            return str(engine.pool.size())
        except Exception:
            return "n/a"

    def _pool_checkedin() -> str:
        try:
            return str(engine.pool.checkedin())
        except Exception:
            return "n/a"

    @sa_event.listens_for(engine.sync_engine, "connect")
    def receive_connect(dbapi_connection, connection_record):
        """Set connection parameters on connect"""
        logger.debug("Database connection established")

    @sa_event.listens_for(engine.sync_engine, "checkout")
    def receive_checkout(dbapi_connection, connection_record, connection_proxy):
        """Log when connection is checked out from pool"""
        if settings.DEBUG:
            logger.debug(
                f"Connection checked out from pool. "
                f"Pool size: {_pool_size()}, "
                f"Checked in: {_pool_checkedin()}"
            )

    @sa_event.listens_for(engine.sync_engine, "close")
    def receive_close(dbapi_connection, connection_record):
        """Log when connection is returned to pool"""
        if settings.DEBUG:
            logger.debug("Database connection closed")


def create_engine() -> AsyncEngine:
    """
    Create async SQLAlchemy engine with production-optimized settings.

    Phase 33 (K-H6) — unconfigured DATABASE_URL: previously
    ``create_async_engine("")`` raised ``ArgumentError`` at import
    time, so the documented dev "no Postgres needed / in-memory
    mode" (``backend/.env.development``: "Set to empty string to use
    in-memory mode") could never actually boot — the app died with
    a cryptic URL-parse error before the lifespan was even reached.
    In non-production, an empty DATABASE_URL now yields a
    single-connection in-memory sqlite placeholder engine instead,
    so the process can import and boot; the lifespan (see
    ``app.main._init_database_or_fail_fast``) deliberately SKIPS
    ``init_db`` in this mode and logs that there is no database —
    the placeholder is an import-time shim, NOT a database the app
    pretends to persist to. Production still refuses: the Settings
    validator fails first, and this function raises again as
    defence-in-depth if it is ever reached with an empty URL.
    A NON-EMPTY url is untouched by this branch: a configured but
    dead database still fails at ``init_db`` time in the lifespan,
    loudly (see ``app.main``).
    """

    # Base engine configuration
    #
    # connect_args is dialect-aware: asyncpg-only kwargs (command_timeout,
    # server_settings) crash every sqlite connect with "invalid keyword
    # argument", so they are only included for postgresql DATABASE_URLs.
    database_url = (settings.DATABASE_URL or "").strip()
    if not database_url:
        if settings.ENVIRONMENT == "production":
            raise RuntimeError(
                "Refusing to create a database engine in production "
                "without DATABASE_URL set. Set DATABASE_URL to the real "
                "connection string."
            )
        logger.warning(
            "DATABASE_URL is not set: no database is configured "
            "(dev/test no-DB mode). Using an in-memory sqlite "
            "placeholder engine so the process can boot; the "
            "lifespan skips init_db in this mode and DB-backed "
            "features will fail per-request instead of persisting. "
            "Set DATABASE_URL to run with a real database."
        )
        from sqlalchemy.pool import StaticPool

        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            echo=settings.DATABASE_ECHO,
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        # Fall through to the shared event-listener registration
        # below, then return early (no pool-size log: StaticPool
        # has no pool_size).
        _register_engine_listeners(engine)
        logger.info("Database engine created | in-memory sqlite placeholder (no DATABASE_URL)")
        return engine

    connect_args: Dict[str, Any] = {}
    if database_url.startswith("postgresql"):
        connect_args = {
            "command_timeout": 30,
            "server_settings": {
                "application_name": "aegis_ai",
                "timezone": "UTC",
                "statement_timeout": "30000",  # 30 second query timeout
            },
        }

    engine_kwargs = {
        "echo": settings.DATABASE_ECHO,
        "echo_pool": settings.DEBUG,
        "pool_size": settings.DATABASE_POOL_SIZE,
        "max_overflow": settings.DATABASE_MAX_OVERFLOW,
        "pool_recycle": settings.DATABASE_POOL_RECYCLE,
        "pool_timeout": settings.DATABASE_POOL_TIMEOUT,
        "pool_pre_ping": True,  # Verify connections before use
        "connect_args": connect_args,
    }

    # Environment-specific configurations
    if settings.ENVIRONMENT == "production":
        engine_kwargs.update({
            "pool_size": 50,
            "max_overflow": 25,
            "pool_timeout": 60,
            "pool_recycle": 1800,  # 30 minutes
            "connect_args": {
                **engine_kwargs["connect_args"],
                "ssl": settings.DATABASE_SSL,
                "keepalives": 1,
                "keepalives_idle": 30,
                "keepalives_interval": 10,
                "keepalives_count": 5,
            },
        })
    elif settings.ENVIRONMENT == "development":
        engine_kwargs.update({
            "echo": settings.DEBUG,
            "pool_size": 10,
            "max_overflow": 5,
        })
    else:  # staging
        engine_kwargs.update({
            "pool_size": 20,
            "max_overflow": 10,
        })

    # Create engine
    engine = create_async_engine(
        database_url,
        **engine_kwargs
    )
    _register_engine_listeners(engine)
    logger.info(
        f"Database engine created | "
        f"Pool Size: {engine_kwargs['pool_size']} | "
        f"Max Overflow: {engine_kwargs['max_overflow']}"
    )
    
    return engine


# Global engine instance
engine = create_engine()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ASYNC SESSION FACTORY
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SESSION MANAGEMENT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency for database sessions.
    
    Usage:
        @app.get("/items")
        async def get_items(db: AsyncSession = Depends(get_db)):
            ...
    """
    async with AsyncSessionLocal() as session:
        # Phase 37 W1-fix (QA D1): the surrogate is COMPUTED in the
        # except block but RAISED after it exits, via
        # ``_raise_clean_surrogate`` — raising inside the block
        # would set ``surrogate.__context__`` to the raw exception
        # (``from None`` clears only ``__cause__``), and raising
        # after the block alone is still not enough when the raw
        # exception is thrown in via ``athrow`` during unwinding
        # (see the helper). The bare re-raise in the generic branch
        # is unchanged by design.
        surrogate = None
        try:
            yield session
            await session.commit()
        except SQLAlchemyError as e:
            await session.rollback()
            logger.error(
                f"Database session error: {scrub_db_error_text(e, secrets=_db_secrets())}",
                exc_info=_scrubbed_exc_info(e),
            )
            # Phase 37 (N-1): re-raise the SCRUBBED surrogate, never
            # the raw exception — a driver message can embed the
            # DATABASE_URL, and FastAPI / Starlette log an unhandled
            # dependency exception with its traceback. The surrogate
            # keeps the SQLAlchemy type, so upstream ``except
            # SQLAlchemyError`` handling behaves exactly as before.
            surrogate = _scrubbed_surrogate(e)
        except Exception as e:
            await session.rollback()
            logger.error(
                f"Unexpected session error: {scrub_db_error_text(e, secrets=_db_secrets())}",
                exc_info=_scrubbed_exc_info(e),
            )
            # Deliberately a BARE re-raise, not a scrubbed surrogate:
            # this branch catches exceptions from the ENDPOINT body
            # (HTTPException status semantics, validation errors,
            # application bugs), which must reach FastAPI untouched —
            # converting an HTTPException would turn a 404 into a
            # 500. A DB error always arrives wrapped as
            # SQLAlchemyError (first branch above); an endpoint that
            # itself formats a raw DB exception into its own
            # exception is that endpoint's bug, and this dependency
            # cannot scrub arbitrary application exceptions without
            # breaking their types. The log line above is scrubbed.
            raise
        finally:
            await session.close()
        if surrogate is not None:
            _raise_clean_surrogate(surrogate)


@asynccontextmanager
async def get_db_context() -> AsyncGenerator[AsyncSession, None]:
    """
    Context manager for database sessions.
    
    Usage:
        async with get_db_context() as db:
            result = await db.execute(query)
    """
    async with AsyncSessionLocal() as session:
        # Phase 37 W1-fix (QA D1): surrogate computed in the except
        # block, raised after it exits — see get_db above.
        surrogate = None
        try:
            yield session
            await session.commit()
        except SQLAlchemyError as e:
            await session.rollback()
            logger.error(
                f"Database context error: {scrub_db_error_text(e, secrets=_db_secrets())}",
                exc_info=_scrubbed_exc_info(e),
            )
            # Phase 37 (N-1): scrubbed surrogate, same type — see
            # get_db above.
            surrogate = _scrubbed_surrogate(e)
        except Exception as e:
            await session.rollback()
            logger.error(
                f"Database context error: {scrub_db_error_text(e, secrets=_db_secrets())}",
                exc_info=_scrubbed_exc_info(e),
            )
            # Bare re-raise by design: this branch catches the
            # CALLER's own exceptions (HTTPException, application
            # errors inside the ``async with`` block), which must
            # propagate untouched — see get_db above.
            raise
        finally:
            await session.close()
        if surrogate is not None:
            _raise_clean_surrogate(surrogate)


async def get_db_with_retry(
    max_retries: int = 3,
    retry_delay: float = 0.5
) -> AsyncGenerator[AsyncSession, None]:
    """
    Database session with retry logic for transient failures.
    """
    last_exception = None
    # Phase 37 W1-fix (QA D1): surrogate computed in the except
    # block, raised after it exits via _raise_clean_surrogate —
    # see get_db above.
    surrogate = None

    for attempt in range(max_retries):
        try:
            async with AsyncSessionLocal() as session:
                yield session
                await session.commit()
                return  # Success
        except (OperationalError, DisconnectionError) as e:
            last_exception = e
            logger.warning(
                f"Database connection attempt {attempt + 1}/{max_retries} failed: "
                f"{scrub_db_error_text(e, secrets=_db_secrets())}"
            )
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay * (2 ** attempt))  # Exponential backoff
            await session.rollback()
        except Exception as e:
            last_exception = e
            await session.rollback()
            logger.error(
                f"Database error (non-retryable): {scrub_db_error_text(e, secrets=_db_secrets())}",
                exc_info=_scrubbed_exc_info(e),
            )
            # Phase 37 (N-1): restore the exception TYPE where it is
            # a SQLAlchemy error (e.g. IntegrityError,
            # ProgrammingError) — Phase 36 collapsed everything to
            # plain Exception, which no production caller depended
            # on (zero callers, grep-verified), but a future caller
            # catching OperationalError/IntegrityError would have
            # silently stopped matching. Non-SQLAlchemy errors still
            # collapse to the plain scrubbed Exception: there is no
            # endpoint body here whose types must survive, and the
            # message is preserved scrubbed.
            if isinstance(e, SQLAlchemyError):
                surrogate = _scrubbed_surrogate(e)
            else:
                surrogate = scrubbed_exception(e, secrets=_db_secrets())
        finally:
            await session.close()
        if surrogate is not None:
            _raise_clean_surrogate(surrogate)

    logger.error(f"All {max_retries} database connection attempts failed")
    if last_exception is None:
        # Degenerate max_retries=0: preserve the prior behaviour
        # (``raise None`` -> TypeError) instead of inventing an error.
        raise last_exception
    # Phase 37 (N-1): last_exception is always OperationalError or
    # DisconnectionError here (the only types the retry branch
    # catches), so the surrogate preserves that type — Phase 36's
    # plain-Exception collapse is undone, with the same scrubbed
    # message and no raw chain. This raise already sits outside any
    # except block (probe-verified clean pre-fix); it goes through
    # the helper anyway so every surrogate raise takes one path.
    _raise_clean_surrogate(_scrubbed_surrogate(last_exception))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DATABASE INITIALIZATION & HEALTH CHECKS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def init_db() -> None:
    """
    Initialize database - create all tables and extensions.
    """
    logger.info("Initializing database...")
    
    # PostgreSQL extensions -- in their own guarded block, run BEFORE table
    # creation: sqlite has no CREATE EXTENSION, and a failed statement would
    # otherwise abort the transaction so create_all never runs. Extensions
    # are best-effort; a failure here never blocks table creation.
    if engine.dialect.name == "postgresql":
        try:
            async with engine.begin() as conn:
                # Enable PostgreSQL extensions
                await conn.execute(text("CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\""))
                await conn.execute(text("CREATE EXTENSION IF NOT EXISTS \"pgcrypto\""))
                await conn.execute(text("CREATE EXTENSION IF NOT EXISTS \"pg_trgm\""))
        except Exception as e:
            logger.info(
                f"Skipping PostgreSQL extensions (non-fatal): "
                f"{scrub_db_error_text(e, secrets=_db_secrets())}"
            )

    # Phase 37 W1-fix (QA D1): surrogate computed in the except
    # block, raised after it exits via _raise_clean_surrogate —
    # see get_db above.
    surrogate = None
    try:
        async with engine.begin() as conn:
            # Create all tables
            await conn.run_sync(Base.metadata.create_all)

        logger.info("✅ Database initialized successfully")
        logger.info(f"Tables created: {len(Base.metadata.tables)}")

    except Exception as e:
        logger.error(
            f"❌ Database initialization failed: {scrub_db_error_text(e, secrets=_db_secrets())}",
            exc_info=_scrubbed_exc_info(e),
        )
        # Phase 37 (N-1): re-raise the SCRUBBED surrogate, never the
        # raw exception. Every failure here is DB-originated
        # (engine.begin / create_all — no endpoint body, no
        # HTTPException semantics to preserve), and both callers
        # catch generic Exception (the lifespan re-scrubs and raises
        # RuntimeError; scripts/init_db.py exits 1), so the type is
        # preserved for SQLAlchemyError and collapsed to a scrubbed
        # plain Exception otherwise, with the message preserved
        # scrubbed.
        if isinstance(e, SQLAlchemyError):
            surrogate = _scrubbed_surrogate(e)
        else:
            surrogate = scrubbed_exception(e, secrets=_db_secrets())
    if surrogate is not None:
        _raise_clean_surrogate(surrogate)


async def close_db() -> None:
    """
    Close all database connections.
    """
    logger.info("Closing database connections...")
    try:
        await engine.dispose()
        logger.info("✅ Database connections closed")
    except Exception as e:
        logger.error(
            f"Error closing database: {scrub_db_error_text(e, secrets=_db_secrets())}",
            exc_info=_scrubbed_exc_info(e),
        )


async def check_db_health() -> Dict[str, Any]:
    """
    Check database health and return status.
    """
    try:
        async with engine.connect() as conn:
            start_time = time.time()
            result = await conn.execute(text("SELECT 1"))
            await result.fetchone()
            latency = (time.time() - start_time) * 1000  # ms
            
            # Get pool status
            pool = engine.pool
            pool_status = {
                "size": pool.size() if hasattr(pool, 'size') else "N/A",
                "checked_in": pool.checkedin() if hasattr(pool, 'checkedin') else "N/A",
                "overflow": pool.overflow() if hasattr(pool, 'overflow') else "N/A",
            }
            
            return {
                "status": "healthy",
                "latency_ms": round(latency, 2),
                "pool": pool_status,
                "timestamp": time.time(),
            }
    except Exception as e:
        logger.error(
            f"Database health check failed: {scrub_db_error_text(e, secrets=_db_secrets())}"
        )
        return {
            "status": "unhealthy",
            "error": scrub_db_error_text(e, secrets=_db_secrets()),
            "timestamp": time.time(),
        }


async def get_db_stats() -> Dict[str, Any]:
    """
    Get database statistics.
    """
    try:
        async with engine.connect() as conn:
            # Table sizes
            result = await conn.execute(text("""
                SELECT 
                    schemaname,
                    tablename,
                    pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) as size,
                    n_live_tup as row_count
                FROM pg_stat_user_tables
                ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC
                LIMIT 20
            """))
            tables = [
                {
                    "schema": row[0],
                    "table": row[1],
                    "size": row[2],
                    "rows": row[3],
                }
                for row in await result.fetchall()
            ]
            
            # Database size
            result = await conn.execute(text(
                "SELECT pg_size_pretty(pg_database_size(current_database()))"
            ))
            db_size = (await result.fetchone())[0]
            
            return {
                "database_size": db_size,
                "tables": tables,
                "timestamp": time.time(),
            }
    except Exception as e:
        logger.error(
            f"Failed to get DB stats: {scrub_db_error_text(e, secrets=_db_secrets())}"
        )
        return {"error": scrub_db_error_text(e, secrets=_db_secrets())}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# BASE REPOSITORY
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

from typing import TypeVar, Generic, Type, List, Optional, Union, Dict, Any
from uuid import UUID
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

ModelType = TypeVar("ModelType", bound=Base)


class BaseRepository(Generic[ModelType]):
    """
    Generic repository with common CRUD operations.
    
    Usage:
        class TournamentRepository(BaseRepository[Tournament]):
            def __init__(self, session: AsyncSession):
                super().__init__(Tournament, session)
    """
    
    def __init__(self, model: Type[ModelType], session: AsyncSession):
        self.model = model
        self.session = session
    
    async def create(self, **kwargs) -> ModelType:
        """Create a new record"""
        instance = self.model(**kwargs)
        self.session.add(instance)
        await self.session.flush()
        await self.session.refresh(instance)
        logger.debug(f"Created {self.model.__name__}: {instance}")
        return instance
    
    async def get(self, id: Union[str, UUID, int]) -> Optional[ModelType]:
        """Get record by ID"""
        query = select(self.model).where(
            and_(
                self.model.id == str(id),
                self.model.is_deleted == False
            )
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()
    
    async def get_or_404(self, id: Union[str, UUID, int]) -> ModelType:
        """Get record or raise exception"""
        instance = await self.get(id)
        if not instance:
            from fastapi import HTTPException
            raise HTTPException(
                status_code=404,
                detail=f"{self.model.__name__} not found"
            )
        return instance
    
    async def get_all(
        self,
        skip: int = 0,
        limit: int = 100,
        order_by: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[ModelType]:
        """Get all records with pagination and filtering"""
        query = select(self.model).where(self.model.is_deleted == False)
        
        if filters:
            for field, value in filters.items():
                if hasattr(self.model, field):
                    query = query.where(getattr(self.model, field) == value)
        
        if order_by:
            if hasattr(self.model, order_by):
                query = query.order_by(getattr(self.model, order_by).desc())
        else:
            query = query.order_by(self.model.created_at.desc())
        
        query = query.offset(skip).limit(limit)
        result = await self.session.execute(query)
        return list(result.scalars().all())
    
    async def update(self, id: Union[str, UUID, int], **kwargs) -> Optional[ModelType]:
        """Update a record"""
        instance = await self.get(id)
        if instance:
            for key, value in kwargs.items():
                if hasattr(instance, key):
                    setattr(instance, key, value)
            await self.session.flush()
            await self.session.refresh(instance)
            logger.debug(f"Updated {self.model.__name__} {id}")
        return instance
    
    async def delete(self, id: Union[str, UUID, int], soft: bool = True) -> bool:
        """Delete a record (soft or hard delete)"""
        instance = await self.get(id)
        if instance:
            if soft:
                instance.soft_delete()
            else:
                await self.session.delete(instance)
            await self.session.flush()
            logger.debug(f"Deleted {self.model.__name__} {id} (soft={soft})")
            return True
        return False
    
    async def count(self, filters: Optional[Dict[str, Any]] = None) -> int:
        """Count records"""
        query = select(func.count()).select_from(self.model).where(
            self.model.is_deleted == False
        )
        if filters:
            for field, value in filters.items():
                if hasattr(self.model, field):
                    query = query.where(getattr(self.model, field) == value)
        result = await self.session.execute(query)
        return result.scalar()
    
    async def exists(self, id: Union[str, UUID, int]) -> bool:
        """Check if record exists"""
        query = select(self.model.id).where(
            and_(
                self.model.id == str(id),
                self.model.is_deleted == False
            )
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none() is not None
    
    async def bulk_create(self, items: List[Dict[str, Any]]) -> List[ModelType]:
        """Bulk create records"""
        instances = [self.model(**item) for item in items]
        self.session.add_all(instances)
        await self.session.flush()
        logger.debug(f"Bulk created {len(instances)} {self.model.__name__} records")
        return instances
    
    async def bulk_update(self, updates: List[Dict[str, Any]]) -> int:
        """Bulk update records"""
        count = 0
        for update_data in updates:
            id = update_data.pop("id", None)
            if id:
                instance = await self.update(id, **update_data)
                if instance:
                    count += 1
        return count