# Database Tour: SQLAlchemy + Alembic + Repositories

This project now uses:

- SQLAlchemy 2.0 typed ORM models
- Alembic migrations for schema changes
- Typed repositories for DB access

## Key Files

- ORM base/session: `apps/api/vivian_api/db/database.py`
- Typed models: `apps/api/vivian_api/models/chat_models.py`
- Typed models: `apps/api/vivian_api/models/identity_models.py`
- Repositories: `apps/api/vivian_api/repositories/chat_repository.py`
- CRUD compatibility facade: `apps/api/vivian_api/crud/chat_crud.py`
- Alembic config: `apps/api/alembic.ini`
- Alembic env: `apps/api/alembic/env.py`
- First migration: `apps/api/alembic/versions/20260208_0001_create_chat_tables.py`
- User/home migration: `apps/api/alembic/versions/20260210_0002_create_client_home_tables.py`
- Table rename migration: `apps/api/alembic/versions/20260210_0004_rename_clients_to_users.py`

## Run Migrations

From `apps/api`:

```bash
alembic -c alembic.ini upgrade head
```

The Docker entrypoint runs this automatically on startup.

## Create a New Migration

From `apps/api`:

```bash
alembic -c alembic.ini revision --autogenerate -m "describe change"
alembic -c alembic.ini upgrade head
```

## Repository Usage

Example in a FastAPI route:

```python
from sqlalchemy.orm import Session
from vivian_api.repositories import ChatRepository, ChatMessageRepository

def create_chat_message(db: Session, chat_id: str, text: str) -> None:
    chats = ChatRepository(db)
    messages = ChatMessageRepository(db)

    chat = chats.get(chat_id)
    if not chat:
        raise ValueError("Chat not found")

    messages.create(chat_id=chat_id, role="user", content=text)
```

## Design Notes

- Keep business logic in routers/services.
- Keep SQL in repositories.
- Keep schema evolution in Alembic revisions.
- Avoid `Base.metadata.create_all` in production paths.
