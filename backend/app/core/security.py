# backend/app/core/security.py
"""
AEGIS AI - Security Manager
JWT authentication, API keys, rate limiting, encryption
"""

import hashlib
import secrets
import time
from typing import Optional, Dict, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass

import jwt
from passlib.context import CryptContext
from cryptography.fernet import Fernet

from ..config import settings, Environment
from ..db.models.enums import UserRole
import structlog

logger = structlog.get_logger("aegis.security")

@dataclass
class TokenPayload:
    """JWT token payload"""
    user_id: str
    username: str
    is_superuser: bool = False
    scopes: list = None
    exp: Optional[datetime] = None
    # Phase 6 RBAC: "admin" | "operator" | "viewer" (+ "director" | "spotter" |
    # "observer" since Phase 16). Legacy tokens issued before roles existed
    # carry no claim — decode_token() derives it from is_superuser/scopes so
    # old tokens keep working.
    role: str = "viewer"
    # Phase 16: tournament/user scoping. tournament_id is None for global
    # (non-tournament) accounts; team_id is set for observer accounts.
    team_id: Optional[str] = None
    tournament_id: Optional[str] = None


#: Roles recognised by the control plane — derived from
#: app.db.models.enums.UserRole so the two can never drift apart.
VALID_ROLES = tuple(r.value for r in UserRole)


def _role_from_legacy_payload(payload: dict) -> str:
    """Resolve the role for a decoded JWT payload.

    Tokens minted after Phase 6 carry an explicit ``role`` claim. Older
    tokens don't — they fall back to the pre-roles rule where the single
    configured operator (is_superuser / "admin" scope) was the admin and
    anything else is a viewer.
    """
    role = payload.get("role")
    if role in VALID_ROLES:
        return role
    if payload.get("is_superuser") or "admin" in (payload.get("scopes") or []):
        return "admin"
    if "operator" in (payload.get("scopes") or []):
        return "operator"
    return "viewer"

class SecurityManager:
    """Security and authentication manager"""
    
    def __init__(self):
        self.pwd_context = CryptContext(
            schemes=["bcrypt"],
            deprecated="auto",
            bcrypt__rounds=settings.BCRYPT_ROUNDS,
        )
        
        # Encryption for sensitive data.
        # The Fernet key MUST be stable across restarts, otherwise anything
        # encrypted (e.g. stored API keys) becomes undecryptable after a reboot.
        # It is loaded from settings.FERNET_KEY (env var FERNET_KEY).
        fernet_key = settings.FERNET_KEY.get_secret_value() if settings.FERNET_KEY else None
        if fernet_key:
            self._fernet = Fernet(fernet_key.encode() if isinstance(fernet_key, str) else fernet_key)
        else:
            if settings.ENVIRONMENT == Environment.PRODUCTION:
                raise RuntimeError(
                    "FERNET_KEY is not set: refusing to boot in production with an "
                    "ephemeral encryption key (encrypted data would be lost on restart). "
                    "Set FERNET_KEY to a stable base64 key, e.g. from `python -c "
                    "'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`."
                )
            logger.warning(
                "FERNET_KEY is not set — using an ephemeral encryption key. "
                "Encrypted data will NOT survive a restart. Set FERNET_KEY for any "
                "environment where encrypted data must persist."
            )
            self._fernet = Fernet(Fernet.generate_key())
        
        # API key hashing
        self._api_key_hasher = hashlib.sha256
        
        # Rate limiting
        self._rate_limits: Dict[str, list] = {}
        
        logger.info("SecurityManager initialized")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # PASSWORD MANAGEMENT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def hash_password(self, password: str) -> str:
        """Hash a password using bcrypt"""
        return self.pwd_context.hash(password)
    
    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        """Verify a password against its hash"""
        return self.pwd_context.verify(plain_password, hashed_password)
    
    def generate_secure_password(self, length: int = 16) -> str:
        """Generate a cryptographically secure random password"""
        alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$%^&*"
        return ''.join(secrets.choice(alphabet) for _ in range(length))
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # JWT TOKEN MANAGEMENT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def create_access_token(
        self,
        user_id: str,
        username: str,
        is_superuser: bool = False,
        scopes: Optional[list] = None,
        expires_delta: Optional[timedelta] = None,
        role: str = "viewer",
        team_id: Optional[str] = None,
        tournament_id: Optional[str] = None,
    ) -> str:
        """Create a JWT access token"""
        if role not in VALID_ROLES:
            raise ValueError(
                f"Unknown role {role!r}: must be one of {', '.join(VALID_ROLES)}."
            )
        if expires_delta:
            expire = datetime.utcnow() + expires_delta
        else:
            expire = datetime.utcnow() + timedelta(
                minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
            )
        
        payload = {
            "sub": user_id,
            "username": username,
            "is_superuser": is_superuser,
            "scopes": scopes or [],
            "role": role,
            "team_id": team_id,
            "tournament_id": tournament_id,
            "exp": expire,
            "iat": datetime.utcnow(),
            "type": "access",
        }
        
        token = jwt.encode(
            payload,
            settings.SECRET_KEY.get_secret_value(),
            algorithm=settings.JWT_ALGORITHM,
        )
        
        return token
    
    def create_refresh_token(
        self,
        user_id: str,
        expires_delta: Optional[timedelta] = None,
    ) -> str:
        """Create a JWT refresh token"""
        if expires_delta:
            expire = datetime.utcnow() + expires_delta
        else:
            expire = datetime.utcnow() + timedelta(
                days=settings.REFRESH_TOKEN_EXPIRE_DAYS
            )
        
        payload = {
            "sub": user_id,
            "exp": expire,
            "iat": datetime.utcnow(),
            "type": "refresh",
        }
        
        token = jwt.encode(
            payload,
            settings.SECRET_KEY.get_secret_value(),
            algorithm=settings.JWT_ALGORITHM,
        )
        
        return token
    
    def decode_token(self, token: str) -> Optional[TokenPayload]:
        """Decode and validate a JWT token.

        Only ``type == "access"`` tokens are accepted here — refresh tokens
        (``create_refresh_token``) are rejected so they can never be used as
        bearer credentials.
        """
        try:
            payload = jwt.decode(
                token,
                settings.SECRET_KEY.get_secret_value(),
                algorithms=[settings.JWT_ALGORITHM],
            )

            if payload.get("type") != "access":
                logger.warning("Token rejected: not an access token")
                return None

            return TokenPayload(
                user_id=payload.get("sub"),
                username=payload.get("username", ""),
                is_superuser=payload.get("is_superuser", False),
                scopes=payload.get("scopes", []),
                role=_role_from_legacy_payload(payload),
                team_id=payload.get("team_id"),
                tournament_id=payload.get("tournament_id"),
                exp=datetime.fromtimestamp(payload.get("exp")),
            )
        except jwt.ExpiredSignatureError:
            logger.warning("Token expired")
            return None
        except jwt.InvalidTokenError as e:
            logger.warning(f"Invalid token: {e}")
            return None
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # API KEY MANAGEMENT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def generate_api_key(self) -> Tuple[str, str]:
        """Generate API key and its hash. Returns (raw_key, hashed_key)"""
        raw_key = f"aegis_{secrets.token_urlsafe(32)}"
        hashed_key = self._hash_api_key(raw_key)
        return raw_key, hashed_key
    
    def _hash_api_key(self, api_key: str) -> str:
        """Hash an API key for storage"""
        return self._api_key_hasher(api_key.encode()).hexdigest()
    
    def verify_api_key(self, raw_key: str, stored_hash: str) -> bool:
        """Verify an API key against its stored hash"""
        return self._hash_api_key(raw_key) == stored_hash
    
    def get_api_key_prefix(self, api_key: str) -> str:
        """Get the prefix of an API key for display"""
        return api_key[:12] + "..."
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # ENCRYPTION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def encrypt_data(self, data: str) -> str:
        """Encrypt sensitive data"""
        return self._fernet.encrypt(data.encode()).decode()
    
    def decrypt_data(self, encrypted_data: str) -> str:
        """Decrypt sensitive data"""
        return self._fernet.decrypt(encrypted_data.encode()).decode()
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # RATE LIMITING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def check_rate_limit(
        self,
        key: str,
        max_requests: int = 100,
        window_seconds: int = 60,
    ) -> bool:
        """Check if request is within rate limit"""
        now = time.time()
        window_start = now - window_seconds
        
        if key not in self._rate_limits:
            self._rate_limits[key] = []
        
        # Remove old entries
        self._rate_limits[key] = [
            t for t in self._rate_limits[key] if t > window_start
        ]
        
        # Check limit
        if len(self._rate_limits[key]) >= max_requests:
            return False
        
        # Add current request
        self._rate_limits[key].append(now)
        return True
    
    def get_rate_limit_remaining(
        self,
        key: str,
        max_requests: int = 100,
        window_seconds: int = 60,
    ) -> int:
        """Get remaining rate limit for a key"""
        now = time.time()
        window_start = now - window_seconds
        
        if key not in self._rate_limits:
            return max_requests
        
        current = len([t for t in self._rate_limits[key] if t > window_start])
        return max(0, max_requests - current)


# Global security instance
security_manager = SecurityManager()