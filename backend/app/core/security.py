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

from ..config import settings
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

class SecurityManager:
    """Security and authentication manager"""
    
    def __init__(self):
        self.pwd_context = CryptContext(
            schemes=["bcrypt"],
            deprecated="auto",
            bcrypt__rounds=settings.BCRYPT_ROUNDS,
        )
        
        # Encryption for sensitive data
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
    ) -> str:
        """Create a JWT access token"""
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
        """Decode and validate a JWT token"""
        try:
            payload = jwt.decode(
                token,
                settings.SECRET_KEY.get_secret_value(),
                algorithms=[settings.JWT_ALGORITHM],
            )
            
            return TokenPayload(
                user_id=payload.get("sub"),
                username=payload.get("username", ""),
                is_superuser=payload.get("is_superuser", False),
                scopes=payload.get("scopes", []),
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