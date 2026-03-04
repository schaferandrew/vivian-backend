from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from vivian_api.db.database import Base
from vivian_api.models.identity_models import Home, User
from vivian_api.repositories.connection_repository import (
    McpCustomServerDefinitionRepository,
)


def _build_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    return TestingSessionLocal()


def test_mcp_custom_server_definition_crud_by_home() -> None:
    db = _build_session()

    home = Home(name="Home", timezone="UTC")
    other_home = Home(name="Other", timezone="UTC")
    user = User(email="creator@example.com", status="active")
    db.add_all([home, other_home, user])
    db.commit()
    db.refresh(home)
    db.refresh(other_home)
    db.refresh(user)

    repo = McpCustomServerDefinitionRepository(db)

    created = repo.create(
        home_id=home.id,
        server_id="custom_alpha",
        name="Custom Alpha",
        description="First custom MCP",
        command_tokens=["python", "-m", "alpha"],
        server_path="/tmp/alpha",
        default_enabled=True,
        source="custom",
        created_by=user.id,
        metadata_json={"team": "qa"},
    )

    fetched = repo.get_by_home_and_server(home.id, "custom_alpha")
    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.command_tokens == ["python", "-m", "alpha"]

    assert repo.get_by_home_and_server(other_home.id, "custom_alpha") is None

    updated = repo.update(
        created,
        name="Custom Alpha Updated",
        command_tokens=["uvx", "alpha"],
        default_enabled=False,
        metadata_json={"team": "platform"},
    )
    assert updated.name == "Custom Alpha Updated"
    assert updated.command_tokens == ["uvx", "alpha"]
    assert updated.default_enabled is False
    assert updated.metadata_json == {"team": "platform"}

    listed = repo.list_by_home(home.id)
    assert len(listed) == 1
    assert listed[0].server_id == "custom_alpha"

    repo.delete(updated)
    assert repo.list_by_home(home.id) == []

    db.close()
