# Agent Instructions (Backend)

## Shared Rules

Use shared assistant rules from:
`/Users/Andrew/Developer/vivian-workspace/assistant-rules`

Primary shared rule files:
- `/Users/Andrew/Developer/vivian-workspace/assistant-rules/rules/github-issue-workflow.mdc`
- `/Users/Andrew/Developer/vivian-workspace/assistant-rules/rules/docker-compose.mdc`

## Repo-Specific Notes

- Prefer backend-focused changes in `apps/api`, `apps/mcp-server`, and `packages/shared`.
- Keep API behavior changes covered by tests where practical.

## Worktree Env Setup

- For new `vivian-backend` worktrees, ensure `.env` exists before running Docker or API tests.
- Canonical source env file: `/Users/Andrew/Developer/vivian-workspace/vivian-backend/.env`
- Worktree destination env file: `<worktree-root>/.env`
- Copy command:
  `cp /Users/Andrew/Developer/vivian-workspace/vivian-backend/.env /Users/Andrew/.codex/worktrees/<worktree-name>/vivian-backend/.env`
- Verify command:
  `ls -l /Users/Andrew/.codex/worktrees/<worktree-name>/vivian-backend/.env`
- If `.env` is missing in a new backend worktree, agents should copy it from the canonical source by default before full validation.
- Do not print or echo `.env` contents in logs or responses.

## Development Workflow

### Python Environment

This project uses **uv** (not venv) for dependency management.

**Run tests:**
```bash
uv run --project apps/api --extra test pytest apps/api/tests
```

**Run MCP server tests:**
```bash
uv run --project apps/test-mcp-server --extra test pytest apps/test-mcp-server/tests
```

### Database Migrations

**Run migrations:**
```bash
cd apps/api && DATABASE_URL="postgresql://postgres:postgres@localhost:5432/vivian" uv run alembic upgrade head
```

**Check status:**
```bash
cd apps/api && DATABASE_URL="postgresql://postgres:postgres@localhost:5432/vivian" uv run alembic current
```

### Container Management

**Restart API container after code changes:**
```bash
docker compose restart api
docker logs vivian-backend-api-1 --tail 20  # Verify startup
```

## Code Review Policy

**Always ask for user approval before committing or opening a PR.** Do not push or open PRs without explicit approval.

If confidence is low, validation is incomplete, or important checks fail, pause and ask for user guidance before pushing.
