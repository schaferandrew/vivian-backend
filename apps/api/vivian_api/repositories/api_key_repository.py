"""Repository for HomeApiKey entities."""

from __future__ import annotations

from datetime import datetime, timezone
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from vivian_api.models.identity_models import HomeApiKey


class ApiKeyRepository:
    """Repository for HomeApiKey entities."""

    def __init__(self, db: Session):
        self.db = db

    def create(
        self,
        *,
        home_id: str,
        name: str,
        key_hash: str,
        key_prefix: str,
        created_by: str | None,
    ) -> HomeApiKey:
        key = HomeApiKey(
            id=str(uuid.uuid4()),
            home_id=home_id,
            name=name,
            key_hash=key_hash,
            key_prefix=key_prefix,
            created_by=created_by,
            created_at=datetime.now(timezone.utc),
            last_used_at=None,
        )
        self.db.add(key)
        self.db.commit()
        self.db.refresh(key)
        return key

    def list_by_home(self, home_id: str) -> list[HomeApiKey]:
        stmt = (
            select(HomeApiKey)
            .where(HomeApiKey.home_id == home_id)
            .order_by(HomeApiKey.created_at.desc())
        )
        return list(self.db.scalars(stmt))

    def get_by_hash(self, key_hash: str) -> HomeApiKey | None:
        stmt = select(HomeApiKey).where(HomeApiKey.key_hash == key_hash)
        return self.db.scalar(stmt)

    def get_by_id(self, key_id: str, home_id: str) -> HomeApiKey | None:
        stmt = select(HomeApiKey).where(
            HomeApiKey.id == key_id,
            HomeApiKey.home_id == home_id,
        )
        return self.db.scalar(stmt)

    def update_last_used(self, key: HomeApiKey) -> None:
        key.last_used_at = datetime.now(timezone.utc)
        self.db.commit()

    def delete(self, key: HomeApiKey) -> None:
        self.db.delete(key)
        self.db.commit()
