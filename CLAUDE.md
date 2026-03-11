# Vivian Backend — Claude Instructions

## Permissions

```json
{
  "permissions": {
    "allow": [
      "Bash(gh issue create:*)",
      "Bash(gh issue list:*)",
      "Bash(gh pr create:*)"
    ]
  }
}
```

## Project Overview

FastAPI backend for the Vivian household agent. PostgreSQL in production, SQLite in tests. All endpoints live under `/api/v1`. Authentication is JWT Bearer tokens obtained from `POST /api/v1/auth/login`.

---

## Shared Rules

### GitHub Issue Feature Workflow

When implementing a new feature, especially work that originates from a GitHub issue:

- Create and use a dedicated branch before making changes.
- Name the branch so it is clearly tied to the issue and feature.
- Prefer branch format: `<agent-or-developer-name>-<issue-number>-<short-feature-slug>` (example: `codex-123-google-integration`)
- Do not implement feature work directly on long-lived branches (`main`, `master`).
- After implementation, **always ask the user for approval before committing and opening a PR**.
- In the PR description, include a closing keyword with the issue number so GitHub auto-closes it on merge:
  - `Closes #<issue-number>` or `Fixes #<issue-number>`.
- If no issue exists yet, create/link one before merge when the work is feature-sized.

### GitHub Issue Management

When creating GitHub issues, use the `gh` CLI:

```bash
gh issue create --title "Issue Title" --body-file /path/to/body.md --label "enhancement"
# Or inline:
gh issue create --title "Issue Title" --body "Issue description here"
```

Issue body structure for features: **Problem**, **Proposed Solution**, **Implementation Considerations**, **Benefits**.
Issue body structure for bugs: **Describe the bug**, **To Reproduce**, **Expected behavior**, **Additional context**.

Check available labels with: `gh label list`

### Docker Compose: Run in Foreground

When suggesting `docker compose up`, **do not use `-d`**.

- Use: `docker compose up api` (or `docker compose up` for all services)
- Avoid: `docker compose up -d api`

### Debugging: Check Logs First

When investigating errors, always check logs across the full stack first.

```bash
# Backend API (Docker)
docker logs vivian-backend-api-1 --since 5m
docker logs vivian-backend-api-1 2>&1 | grep -B5 -A10 "Error\|Traceback\|500\|exception"
docker logs -f vivian-backend-api-1

# PostgreSQL
docker logs vivian-backend-postgres-1 --since 5m
```

Common pitfalls: stale containers after code changes, env var prefix `VIVIAN_API_`, Next.js caching.

### Use Shared Helper Functions

When writing code involving text normalization, date parsing, or common string operations, use shared helpers from `vivian_shared.helpers`:

```python
from vivian_shared.helpers import normalize_provider, normalize_title, normalize_header
from vivian_shared.helpers import parse_date, days_between, is_within_days
```

Related files:
- `packages/shared/src/vivian_shared/helpers/normalization.py`
- `packages/shared/src/vivian_shared/helpers/dates.py`

---

## Development Workflow

### Python Environment

This project uses **uv** for dependency management (not venv).

**Run tests:**
```bash
uv run --project apps/api --extra test pytest apps/api/tests
```

**Run MCP server tests:**
```bash
uv run --project apps/test-mcp-server --extra test pytest apps/test-mcp-server/tests
```

### Database Migrations

The project uses Alembic for database schema management.

**Run pending migrations:**
```bash
cd apps/api && DATABASE_URL="postgresql://postgres:postgres@localhost:5432/vivian" uv run alembic upgrade head
```

**Check current migration version:**
```bash
cd apps/api && DATABASE_URL="postgresql://postgres:postgres@localhost:5432/vivian" uv run alembic current
```

**Important notes:**
- The backend container automatically runs migrations on startup via the entrypoint script.
- When developing locally and pulling new branches, you may need to run migrations manually.
- In `.env`, the database hostname is `postgres:5432` (for container-to-container communication).

### After Merging New Code

When you merge a feature branch that includes new API endpoints or database changes:

1. **Run database migrations** (if schema changed):
   ```bash
   cd apps/api && DATABASE_URL="postgresql://postgres:postgres@localhost:5432/vivian" uv run alembic upgrade head
   ```

2. **Restart the backend container** to pick up new code:
   ```bash
   docker compose restart api
   ```

3. **Verify the endpoint** is working:
   ```bash
   docker logs vivian-backend-api-1 --tail 20
   ```

### Common Issues

#### "Failed to load" errors in frontend
- Usually means the backend database hasn't been migrated yet.
- Run `alembic upgrade head` to apply pending migrations.

#### Duplicate table/index errors in migrations
- Don't use `index=True` on Column AND `op.create_index()` for the same column — choose one.

#### Backend container has stale code
- Always restart the API container after merging: `docker compose restart api`

---

## Worktree Env Setup

For new `vivian-backend` worktrees, ensure `.env` exists before running Docker or API tests.

- Canonical source env file: `/Users/Andrew/Developer/vivian-workspace/vivian-backend/.env`
- Copy command: `cp /Users/Andrew/Developer/vivian-workspace/vivian-backend/.env <worktree-root>/.env`
- Do not print or echo `.env` contents in logs or responses.

---

## Code Review Policy

**Always ask the user for approval before committing or opening a PR.** Do not commit or push without explicit approval.

---

## Link Settings API

Link settings store the home server's base URL and per-app port numbers so the frontend can render quick-access links to self-hosted services (Jellyfin, Mealie, photo host, etc.).

### Design

- **One `server_url` entry** stores the home server's base address (e.g. `http://192.168.1.10`).
- **Per-app entries** store only a `port` integer. The frontend combines `server_url` + `:` + `port` to build the full link.
- If an app's port is `null`, that link is hidden in the UI — the user hasn't configured it yet.
- Settings are scoped per home and inferred from the authenticated user's default home — **no `home_id` param needed**.

### Base URL

```
/api/v1/link-settings
```

### Authentication

All endpoints require a Bearer token:

```
Authorization: Bearer <access_token>
```

### Well-known keys

| Key          | Service               | Field to set |
|--------------|-----------------------|--------------|
| `server_url` | Home server base IP   | `url`        |
| `jellyfin`   | Jellyfin media server | `port`       |
| `mealie`     | Mealie recipe manager | `port`       |
| `images`     | Photo / image host    | `port`       |
