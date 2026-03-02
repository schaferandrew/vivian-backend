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

Link settings store named URLs for household services (Jellyfin, Mealie, image hosts, etc.) so the frontend can retrieve and display them as quick-access links. Settings are scoped per home and inferred automatically from the authenticated user's default home — no `home_id` param is needed in requests.

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
{
  id: string           // UUID — stable row identifier
  home_id: string      // UUID — the home this belongs to
  key: string          // Slug used to look up a specific service, e.g. "jellyfin"
  label: string        // Human-readable name shown in UI, e.g. "Jellyfin"
  url: string          // Full base URL, e.g. "http://192.168.1.10:8096"
  icon: string | null  // Optional icon identifier or URL
  created_at: string   // ISO 8601 datetime
  updated_at: string   // ISO 8601 datetime
}
```

The `key` is the stable identifier — use it to look up a specific service by name (e.g. always fetch the Jellyfin link by key `"jellyfin"`).

---

### Endpoints

#### List all link settings

```
GET /api/v1/link-settings
```

Returns all link settings for the current user's home, sorted by key.

**Response `200`**
```json
[
  {
    "id": "abc123...",
    "home_id": "def456...",
    "key": "jellyfin",
    "label": "Jellyfin",
    "url": "http://192.168.1.10:8096",
    "icon": null,
    "created_at": "2026-03-02T12:00:00",
    "updated_at": "2026-03-02T12:00:00"
  }
]
```

---

#### Create a link setting

```
POST /api/v1/link-settings
```

**Request body**
```json
{
  "key": "jellyfin",
  "label": "Jellyfin",
  "url": "http://192.168.1.10:8096",
  "icon": null
}
```

| Field   | Type            | Required | Notes                              |
|---------|-----------------|----------|------------------------------------|
| `key`   | string (≤100)   | Yes      | Slug — must be unique per home     |
| `label` | string (≤255)   | Yes      | Display name                       |
| `url`   | string          | Yes      | Full URL including scheme and port |
| `icon`  | string \| null  | No       | Icon name or URL                   |

**Response `201`** — the created `LinkSetting` object.

**Error `409`** — key already exists for this home.

---

#### Update a link setting

```
PUT /api/v1/link-settings/{key}
```

All body fields are optional; only provided fields are updated.

**Request body**
```json
{
  "label": "My Jellyfin",
  "url": "http://192.168.1.20:8096",
  "icon": "film"
}
```

**Response `200`** — the updated `LinkSetting` object.

**Error `404`** — no setting with that key exists for this home.

---

#### Delete a link setting

```
DELETE /api/v1/link-settings/{key}
```

**Response `204`** — no body.

**Error `404`** — no setting with that key exists for this home.

---

### Suggested well-known keys

| Key        | Service               |
|------------|-----------------------|
| `jellyfin` | Jellyfin media server |
| `mealie`   | Mealie recipe manager |
| `images`   | Photo / image host    |
| `home`     | Home dashboard        |

These are conventions only — any string key is valid.

---

### Example: fetch and open a service link

```ts
// Fetch all links once (e.g. on app load)
const res = await fetch('/api/v1/link-settings', {
  headers: { Authorization: `Bearer ${accessToken}` },
});
const links = await res.json(); // LinkSetting[]

// Look up by key
const jellyfin = links.find(l => l.key === 'jellyfin');
if (jellyfin) window.open(jellyfin.url, '_blank');
```

### Example: settings form — save a new link

```ts
await fetch('/api/v1/link-settings', {
  method: 'POST',
  headers: {
    Authorization: `Bearer ${accessToken}`,
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({ key: 'mealie', label: 'Mealie', url: 'http://192.168.1.10:9000' }),
});
```

### Example: update an existing link

```ts
await fetch('/api/v1/link-settings/mealie', {
  method: 'PUT',
  headers: {
    Authorization: `Bearer ${accessToken}`,
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({ url: 'http://192.168.1.11:9000' }),
});
```
