# backend/app/config.py
"""
AEGIS AI - Central Configuration System
Production-grade settings with comprehensive validation
"""

import logging
import logging.config
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from enum import Enum
from pydantic_settings import BaseSettings
from pydantic import Field, SecretStr, field_validator

class Environment(str, Enum):
    DEVELOPMENT = "development"
    STAGING = "staging" 
    PRODUCTION = "production"

class BGMI_MAP(str, Enum):
    ERANGEL = "erangel"
    MIRAMAR = "miramar"
    SANHOK = "sanhok"
    VIKENDI = "vikendi"
    LIVIK = "livik"
    KARAKIN = "karakin"

class AgentPriority(int, Enum):
    CRITICAL = 100
    HIGH = 80
    MEDIUM = 60
    LOW = 40
    BACKGROUND = 20

class Settings(BaseSettings):
    """Central configuration with comprehensive validation"""
    
    # Environment
    ENVIRONMENT: Environment = Environment.DEVELOPMENT
    DEBUG: bool = Field(default=False, description="Enable debug mode")
    PROJECT_NAME: str = "AEGIS AI"
    VERSION: str = "1.0.0"
    API_V1_PREFIX: str = "/api/v1"

    
    # Server Configuration
    HOST: str = Field(default="0.0.0.0", description="Server host")
    PORT: int = Field(default=8000, ge=1, le=65535, description="Server port")
    WORKERS: int = Field(default=4, ge=1, le=32, description="Uvicorn workers")
    MAX_CONNECTIONS: int = Field(default=1000, ge=1, description="Max connections")
    REQUEST_TIMEOUT: int = Field(default=30, ge=1, description="Request timeout seconds")
    KEEPALIVE_TIMEOUT: int = Field(default=65, ge=1, description="Keepalive timeout")
    
    # Security
    SECRET_KEY: SecretStr = Field(default="change-me-in-production-please-very-long-secret")
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = Field(default=1440, ge=1, description="JWT expiry in minutes")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=30, ge=1)
    REFRESH_TOKEN_EXPIRE_DAYS: int = Field(default=7, ge=1)
    API_KEY_HEADER: str = "X-API-Key"
    BCRYPT_ROUNDS: int = Field(default=12, ge=4, le=31)
    # Fernet key for at-rest encryption of sensitive data (e.g. stored API keys).
    # MUST be set to a stable value in production; if unset, a random ephemeral
    # key is used and anything encrypted becomes undecryptable after restart.
    FERNET_KEY: Optional[SecretStr] = Field(default=None, description="Base64 Fernet key for at-rest encryption")
    # Operator auth (Phase 1): there is no User table yet, so the control plane
    # authenticates against a single configured admin credential and/or a
    # static API-key allowlist. Set ADMIN_PASSWORD (and keep ADMIN_USERNAME
    # secret if you change it) and/or ADMIN_API_KEYS in the environment.
    # Treat these as real credentials: never commit them.
    ADMIN_USERNAME: str = Field(default="admin", description="Operator username for /api/v1/auth/token login")
    ADMIN_PASSWORD: Optional[SecretStr] = Field(default=None, description="Operator password; login disabled when unset. ALSO seeds the first DB admin together with ADMIN_EMAIL (Phase 6).")
    ADMIN_EMAIL: Optional[str] = Field(default=None, description="Email of the first admin user. When ADMIN_EMAIL and ADMIN_PASSWORD are both set and no admin user exists in the DB, one is seeded at startup (bcrypt-hashed, never plaintext).")
    ADMIN_API_KEYS: List[str] = Field(default=[], description="Static allowlist of operator API keys (dev/demo grade)")
    
    # Database
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://aegis:aegis123@localhost:5432/aegis",
        description="Database connection URL"
    )
    DATABASE_POOL_SIZE: int = Field(default=20, ge=5, le=100)
    DATABASE_MAX_OVERFLOW: int = Field(default=10, ge=0, le=50)
    DATABASE_POOL_RECYCLE: int = Field(default=3600, ge=60)
    DATABASE_POOL_TIMEOUT: int = Field(default=30, ge=1)
    DATABASE_ECHO: bool = False
    DATABASE_SSL: bool = False
    
    # Redis / Event Bus
    REDIS_URL: str = Field(default="redis://localhost:6379/0")
    REDIS_MAX_CONNECTIONS: int = Field(default=50, ge=10, le=500)
    REDIS_STREAM_MAX_LEN: int = Field(default=10000, ge=100, description="Max events in stream")
    REDIS_EVENT_TTL: int = Field(default=86400, ge=3600, description="Event TTL seconds")
    REDIS_SOCKET_TIMEOUT: int = Field(default=5, ge=1)
    REDIS_RETRY_ON_TIMEOUT: bool = True

    # Distributed cluster identity (Phase 7 / W3).
    # NODE_ROLES: comma-separated roles this node serves. Valid: api,
    # ai-worker, streamer. All nodes must share REDIS_URL (+ DATABASE_URL)
    # for the cluster registry to see each other.
    NODE_ROLES: str = Field(
        default="api,ai-worker",
        description="Comma-separated roles this node serves: api, ai-worker, streamer",
    )
    # NODE_ID: stable override for this node's registry id. When unset, a
    # fresh uuid4 hex is generated per process start (so N workers on one
    # host appear as N distinct nodes instead of clobbering one key).
    NODE_ID: Optional[str] = Field(
        default=None,
        description="Stable node id override; default is uuid4 hex per process start",
    )
    
    # GPU / Computer Vision
    CUDA_DEVICES: List[int] = Field(default=[0])
    USE_TENSORRT: bool = Field(default=True, description="Use TensorRT optimization")
    MODEL_PRECISION: str = Field(default="fp16", description="fp32, fp16, int8")
    YOLO_MODEL_PATH: str = Field(default="models/yolov8m_bgmi.pt")
    YOLO_CONFIDENCE_THRESHOLD: float = Field(default=0.5, ge=0.0, le=1.0)
    YOLO_IOU_THRESHOLD: float = Field(default=0.45, ge=0.0, le=1.0)
    PADDLEOCR_MODEL_PATH: str = Field(default="models/paddleocr")
    BYTETRACK_MODEL_PATH: str = Field(default="models/bytetrack")
    
    # Spectator Devices
    MAX_SPECTATOR_DEVICES: int = Field(default=16, ge=1, le=20)
    DEVICE_FPS_HIGH_PRIORITY: int = Field(default=30, ge=1, le=60)
    DEVICE_FPS_LOW_PRIORITY: int = Field(default=5, ge=1, le=30)
    DEVICE_CONNECTION_TIMEOUT: int = Field(default=10, ge=1)
    DEVICE_HEALTH_CHECK_INTERVAL: int = Field(default=5, ge=1)
    DEVICE_RECONNECT_MAX_RETRIES: int = Field(default=3, ge=1)
    
    # Agent System
    MAX_TEAM_AGENTS: int = Field(default=16, ge=1, le=20)
    AGENT_TICK_INTERVAL_MS: int = Field(default=100, ge=10, le=1000)
    AGENT_MAX_ERRORS: int = Field(default=5, ge=1, le=20)
    AGENT_ERROR_COOLDOWN_MS: int = Field(default=5000, ge=1000)
    AGENT_HEALTH_CHECK_MS: int = Field(default=5000, ge=1000)
    AGENT_MESSAGE_QUEUE_SIZE: int = Field(default=1000, ge=100)
    
    # Fight Detection
    FIGHT_DETECTION_RADIUS_METERS: int = Field(default=200, ge=50, le=1000)
    FIGHT_HP_DROP_THRESHOLD: int = Field(default=20, ge=5, le=100)
    FIGHT_PROXIMITY_THRESHOLD: int = Field(default=100, ge=10, le=500)
    FIGHT_MIN_DURATION_SECONDS: int = Field(default=3, ge=1, le=10)
    FIGHT_INTENSITY_SCORE_THRESHOLD: int = Field(default=30, ge=1, le=100)
    FIGHT_COOLDOWN_SECONDS: int = Field(default=5, ge=1, le=30)
    
    # OBS / Broadcast
    OBS_WS_URL: str = Field(default="ws://localhost:4455")
    OBS_PASSWORD: Optional[str] = None
    OBS_RECONNECT_INTERVAL: int = Field(default=5, ge=1)
    MIN_SCENE_TIME_SECONDS: int = Field(default=10, ge=3, le=60)
    MAX_SWITCHES_PER_MINUTE: int = Field(default=8, ge=1, le=30)
    BROADCAST_LATENCY_MS: int = Field(default=3000, ge=0, le=10000)
    
    # Commentary
    LLM_MODEL: str = Field(default="meta-llama/Llama-2-7b-chat-hf")
    LLM_ENDPOINT: str = Field(default="http://localhost:8001/v1")
    LLM_MAX_TOKENS: int = Field(default=150, ge=50, le=500)
    LLM_TEMPERATURE: float = Field(default=0.7, ge=0.0, le=2.0)
    ELEVENLABS_API_KEY: Optional[str] = None
    COMMENTARY_LANGUAGE: str = Field(default="hindi")
    COMMENTARY_DELAY_MS: int = Field(default=2000, ge=0, le=5000)
    
    # BGMI Game Constants
    BGMI_MAP_SIZE: Dict[str, Tuple[int, int]] = {
        "erangel": (8000, 8000),
        "miramar": (8000, 8000),
        "sanhok": (4000, 4000),
        "vikendi": (6000, 6000),
        "livik": (2000, 2000),
        "karakin": (2000, 2000),
    }
    BGMI_MAX_PLAYERS: int = 64
    BGMI_MAX_TEAMS: int = 16
    BGMI_ZONE_PHASES: int = 8
    BGMI_PLAYERS_PER_TEAM: int = 4
    
    # Tournament Rules
    # NOTE (Phase 13): scoring lives in the tournament master data
    # (Tournament.points_per_kill / Tournament.placement_points), read via
    # PointsCalculator.configure_from_active_tournament(). The old hardcoded
    # POINTS_PER_KILL / PLACEMENT_POINTS settings were removed: they had zero
    # readers and contradicted per-tournament configs. Do not reintroduce.
    
    # Paths
    BASE_DIR: Path = Path(__file__).parent.parent
    DATA_DIR: Path = BASE_DIR / "data"
    MODELS_DIR: Path = DATA_DIR / "models"
    RECORDINGS_DIR: Path = DATA_DIR / "recordings"
    HIGHLIGHTS_DIR: Path = DATA_DIR / "highlights"
    LOGS_DIR: Path = DATA_DIR / "logs"
    TEMP_DIR: Path = DATA_DIR / "temp"
    
    # CORS
    # Default to explicit local origins. Wildcard "*" is supported but MUST NOT
    # be combined with allow_credentials (browsers reject it); main.py disables
    # allow_credentials automatically when the origin list is exactly ["*"].
    CORS_ORIGINS: List[str] = Field(default=["http://localhost:3000", "http://localhost:3001"])
    CORS_METHODS: List[str] = Field(default=["*"])
    CORS_HEADERS: List[str] = Field(default=["*"])
    
    # Monitoring
    ENABLE_METRICS: bool = True
    METRICS_PORT: int = Field(default=9090, ge=1, le=65535)
    ENABLE_TRACING: bool = False
    OTEL_EXPORTER_ENDPOINT: Optional[str] = None
    
    # Feature Flags
    ENABLE_AI_COMMENTARY: bool = True
    ENABLE_FIGHT_PREDICTION: bool = True
    ENABLE_AUTO_HIGHLIGHTS: bool = True
    ENABLE_CHEAT_DETECTION: bool = False  # Phase 2
    # NOTE (Phase 13): the demo-mode gate lives in app/main.py as the
    # module-level DEMO_MODE env flag (default OFF) — kept there so the
    # simulator and inline demo endpoints share one source of truth.
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True
        use_enum_values = True
    
    @field_validator("CUDA_DEVICES", mode="before")
    @classmethod
    def parse_cuda_devices(cls, v):
        if isinstance(v, str):
            return [int(x.strip()) for x in v.split(",") if x.strip()]
        return v
    
    def model_post_init(self, __context):
        """Create all required directories"""
        for dir_path in [
            self.DATA_DIR, self.MODELS_DIR, self.RECORDINGS_DIR,
            self.HIGHLIGHTS_DIR, self.LOGS_DIR, self.TEMP_DIR
        ]:
            dir_path.mkdir(parents=True, exist_ok=True)


# Global settings instance
settings = Settings()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LOGGING CONFIGURATION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {
            "format": (
                "%(asctime)s.%(msecs)03d | %(levelname)-8s | "
                "%(name)s:%(lineno)d | %(funcName)s() | %(message)s"
            ),
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
        "json": {
            "()": "pythonjsonlogger.jsonlogger.JsonFormatter",
            "format": (
                "%(asctime)s %(levelname)s %(name)s "
                "%(funcName)s %(lineno)d %(message)s"
            ),
        },
        "access": {
            "format": (
                "%(asctime)s | %(levelname)s | "
                "%(client_addr)s | %(request_line)s | %(status_code)s"
            ),
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "default",
            "stream": "ext://sys.stdout",
            "level": "DEBUG",
        },
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "formatter": "json",
            "filename": str(settings.LOGS_DIR / "aegis.log"),
            "maxBytes": 10485760,  # 10 MB
            "backupCount": 10,
            "level": "INFO",
        },
        "error_file": {
            "class": "logging.handlers.RotatingFileHandler",
            "formatter": "json",
            "filename": str(settings.LOGS_DIR / "errors.log"),
            "maxBytes": 10485760,
            "backupCount": 5,
            "level": "ERROR",
        },
        "agent_file": {
            "class": "logging.handlers.RotatingFileHandler",
            "formatter": "json",
            "filename": str(settings.LOGS_DIR / "agents.log"),
            "maxBytes": 10485760,
            "backupCount": 10,
            "level": "DEBUG",
        },
        "vision_file": {
            "class": "logging.handlers.RotatingFileHandler",
            "formatter": "json",
            "filename": str(settings.LOGS_DIR / "vision.log"),
            "maxBytes": 10485760,
            "backupCount": 10,
            "level": "DEBUG",
        },
    },
    "loggers": {
        "aegis": {
            "level": "DEBUG" if settings.DEBUG else "INFO",
            "handlers": ["console", "file", "error_file"],
            "propagate": False,
        },
        "aegis.agents": {
            "level": "DEBUG",
            "handlers": ["agent_file", "error_file"],
            "propagate": False,
        },
        "aegis.vision": {
            "level": "INFO",
            "handlers": ["vision_file", "error_file"],
            "propagate": False,
        },
        "aegis.api": {
            "level": "INFO",
            "handlers": ["console", "file"],
            "propagate": False,
        },
        "uvicorn": {
            "level": "INFO",
            "handlers": ["console"],
            "propagate": False,
        },
        "uvicorn.access": {
            "level": "WARNING",
            "handlers": ["console"],
            "propagate": False,
        },
    },
    "root": {
        "level": "INFO",
        "handlers": ["console", "file"],
    },
}

logging.config.dictConfig(LOGGING_CONFIG)

# Create logger instances
import logging
logger = logging.getLogger("aegis")
agent_logger = logging.getLogger("aegis.agents")
vision_logger = logging.getLogger("aegis.vision")
api_logger = logging.getLogger("aegis.api")
