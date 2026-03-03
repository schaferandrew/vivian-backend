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

---

### Data Shape

#### `LinkSetting` object

```ts
type LinkSetting = {
  id: string           // UUID — stable row identifier
  home_id: string      // UUID — the home this belongs to
  key: string          // Slug: "server_url" | "jellyfin" | "mealie" | "images" | ...
  label: string        // Display name shown in UI, e.g. "Jellyfin"
  url: string | null   // Full URL — only set on the server_url entry (or direct links)
  port: number | null  // Port number — set on app entries; null means link is hidden
  icon: string | null  // Optional icon identifier or URL
  created_at: string   // ISO 8601 datetime
  updated_at: string   // ISO 8601 datetime
}
```

---

### Endpoints

#### List all link settings

```
GET /api/v1/link-settings
```

Returns all entries for the current home, sorted alphabetically by key.

**Response `200`**
```json
[
  {
    "id": "...",
    "home_id": "...",
    "key": "jellyfin",
    "label": "Jellyfin",
    "url": null,
    "port": 8096,
    "icon": null,
    "created_at": "2026-03-02T12:00:00",
    "updated_at": "2026-03-02T12:00:00"
  },
  {
    "key": "mealie",
    "label": "Mealie",
    "url": null,
    "port": null,
    "...": "..."
  },
  {
    "key": "server_url",
    "label": "Home Server",
    "url": "http://192.168.1.10",
    "port": null,
    "...": "..."
  }
]
```

---

#### Create a link setting

```
POST /api/v1/link-settings
```

Provide `url` for the base server entry, or `port` for app entries.

**Request body**
```json
{ "key": "jellyfin", "label": "Jellyfin", "port": 8096 }
```
```json
{ "key": "server_url", "label": "Home Server", "url": "http://192.168.1.10" }
```

| Field   | Type           | Required | Notes                                   |
|---------|----------------|----------|-----------------------------------------|
| `key`   | string (≤100)  | Yes      | Must be unique per home                 |
| `label` | string (≤255)  | Yes      | Display name                            |
| `url`   | string \| null | No       | Full URL (for `server_url` key)         |
| `port`  | int \| null    | No       | 1–65535 (for app keys like `jellyfin`)  |
| `icon`  | string \| null | No       | Icon name or URL                        |

**Response `201`** — the created `LinkSetting` object.
**Error `409`** — key already exists for this home.

---

#### Update a link setting

```
PUT /api/v1/link-settings/{key}
```

All body fields are optional; only provided fields are updated.

**Response `200`** — updated `LinkSetting` object.
**Error `404`** — key not found.

---

#### Delete a link setting

```
DELETE /api/v1/link-settings/{key}
```

**Response `204`** — no body.
**Error `404`** — key not found.

---

### Frontend Integration Pattern

#### 1. On settings load — fetch all link settings

```ts
const res = await fetch('/api/v1/link-settings', {
  headers: { Authorization: `Bearer ${accessToken}` },
});
const settings: LinkSetting[] = await res.json();

// Index by key for easy lookup
const byKey = Object.fromEntries(settings.map(s => [s.key, s]));
const serverUrl = byKey['server_url']?.url ?? '';
```

#### 2. Build a full URL from server + port

```ts
function buildUrl(serverUrl: string, port: number): string {
  return `${serverUrl}:${port}`;
}
```

#### 3. Render only configured links

```ts
const apps = [
  { key: 'jellyfin', label: 'Jellyfin' },
  { key: 'mealie',   label: 'Mealie'   },
  { key: 'images',   label: 'Photos'   },
];

const visibleLinks = apps
  .filter(app => byKey[app.key]?.port != null)
  .map(app => ({
    label: byKey[app.key].label,
    url: buildUrl(serverUrl, byKey[app.key].port!),
    icon: byKey[app.key].icon,
  }));
```

#### 4. Settings form — save or update a port

```ts
// On first save (no entry yet)
await fetch('/api/v1/link-settings', {
  method: 'POST',
  headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
  body: JSON.stringify({ key: 'jellyfin', label: 'Jellyfin', port: 8096 }),
});

// On subsequent save (entry exists — PUT to the key)
await fetch('/api/v1/link-settings/jellyfin', {
  method: 'PUT',
  headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
  body: JSON.stringify({ port: 8920 }),
});
```

#### 5. Clear a link (hide it from UI)

```ts
await fetch('/api/v1/link-settings/jellyfin', {
  method: 'DELETE',
  headers: { Authorization: `Bearer ${token}` },
});
```

---

### Well-known keys

| Key          | Service               | Field to set |
|--------------|-----------------------|--------------|
| `server_url` | Home server base IP   | `url`        |
| `jellyfin`   | Jellyfin media server | `port`       |
| `mealie`     | Mealie recipe manager | `port`       |
| `images`     | Photo / image host    | `port`       |

Any other key is valid — the list above is just convention.

---

## Development Workflow

### Python Environment

This project uses a Python virtual environment (venv) located at `.venv/` in the repository root.

**Activate the virtual environment:**
```bash
source .venv/bin/activate
```

### Database Migrations

The project uses Alembic for database schema management.

**Check current migration version:**
```bash
cd apps/api
source ../../.venv/bin/activate
alembic current
```

**Run pending migrations:**
```bash
cd apps/api
source ../../.venv/bin/activate
DATABASE_URL="postgresql://postgres:postgres@localhost:5432/vivian" alembic upgrade head
```

**Important notes:**
- The backend container automatically runs migrations on startup via the entrypoint script
- When developing locally and pulling new branches, you may need to run migrations manually
- The `DATABASE_URL` environment variable defaults to `localhost:5432` which maps to the Docker postgres container
- In `.env`, the database hostname is `postgres:5432` (for container-to-container communication)

### After Merging New Code

When you merge a feature branch that includes new API endpoints or database changes:

1. **Run database migrations** (if schema changed):
   ```bash
   cd apps/api
   source ../../.venv/bin/activate
   DATABASE_URL="postgresql://postgres:postgres@localhost:5432/vivian" alembic upgrade head
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
- Usually means the backend database hasn't been migrated yet
- Check `alembic current` vs `alembic heads` to see if migrations are pending
- Run `alembic upgrade head` to apply pending migrations

#### Duplicate table/index errors in migrations
- Alembic Column definitions with `index=True` automatically create indexes
- Don't explicitly call `op.create_index()` for the same column - this creates a duplicate
- **Example bug:**
  ```python
  # BAD - creates index twice
  sa.Column("home_id", UUID, ForeignKey("homes.id"), index=True),  # Creates ix_table_home_id
  op.create_index("ix_table_home_id", "table", ["home_id"])       # Duplicate!

  # GOOD - only create once
  sa.Column("home_id", UUID, ForeignKey("homes.id")),
  op.create_index("ix_table_home_id", "table", ["home_id"])
  ```

#### Backend container has stale code
- The container doesn't automatically reload when you merge branches
- Always restart the API container after merging: `docker compose restart api`
