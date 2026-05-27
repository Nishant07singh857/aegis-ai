# backend/scripts/create_superuser.py
"""
AEGIS AI - Superuser Creation Script
Creates an admin superuser for the AEGIS AI dashboard.
"""

import asyncio
import sys
import uuid
from pathlib import Path
import getpass

sys.path.insert(0, str(Path(__file__).parent.parent))

import structlog
from sqlalchemy import select

from app.db.base import AsyncSessionLocal
from app.db.models import User
from app.core.security import security_manager

logger = structlog.get_logger("aegis.scripts.create_superuser")


async def create_superuser(
    email: str,
    username: str,
    password: str,
    full_name: str,
) -> bool:
    """Create a superuser account"""
    
    async with AsyncSessionLocal() as session:
        # Check if user exists
        result = await session.execute(
            select(User).where(
                (User.email == email) | (User.username == username)
            )
        )
        existing = result.scalar_one_or_none()
        
        if existing:
            logger.error(f"User already exists with email '{email}' or username '{username}'")
            return False
        
        # Create user
        user = User(
            id=str(uuid.uuid4()),
            email=email,
            username=username,
            hashed_password=security_manager.hash_password(password),
            full_name=full_name,
            is_active=True,
            is_superuser=True,
            is_verified=True,
            email_verified_at=datetime.utcnow(),
        )
        
        session.add(user)
        await session.commit()
        
        logger.info(f"Superuser created: {username} ({email})")
        return True


async def main():
    """Main function"""
    
    logger.info("=" * 60)
    logger.info("AEGIS AI - Create Superuser")
    logger.info("=" * 60)
    
    print("\nCreate a superuser account for the AEGIS AI dashboard.\n")
    
    # Get user input
    email = input("Email: ").strip()
    username = input("Username: ").strip()
    password = getpass.getpass("Password: ")
    confirm_password = getpass.getpass("Confirm Password: ")
    full_name = input("Full Name: ").strip()
    
    # Validate
    if not all([email, username, password, full_name]):
        logger.error("All fields are required")
        sys.exit(1)
    
    if password != confirm_password:
        logger.error("Passwords do not match")
        sys.exit(1)
    
    if len(password) < 8:
        logger.error("Password must be at least 8 characters")
        sys.exit(1)
    
    if "@" not in email:
        logger.error("Invalid email address")
        sys.exit(1)
    
    # Create superuser
    success = await create_superuser(email, username, password, full_name)
    
    if success:
        print("\n✅ Superuser created successfully!")
        print(f"   Username: {username}")
        print(f"   Email: {email}")
    else:
        print("\n❌ Failed to create superuser")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())