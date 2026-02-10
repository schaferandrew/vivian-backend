# Vivian Household Agent

A local-first, cloud-optional household agent platform built for personal use. The first implemented skill is HSA expense tracking and reimbursement management.

## Architecture

```
Next.js Frontend (separate)
    ↓ HTTPS
FastAPI Agent API (localhost:8000)
    ↓ stdio
MCP Server - household-mcp
    ↓ REST APIs
Google Drive + Google Sheets
```

## Prerequisites

- Python 3.11+
- Google Cloud project with OAuth 2.0 credentials
- OpenRouter API key
- Google Drive folders for receipt storage

## Setup

### 1. Google Cloud Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select existing
3. Enable APIs:
   - Google Drive API
   - Google Sheets API
4. Create OAuth 2.0 credentials (Desktop application type)
5. Download credentials and note:
   - Client ID
   - Client Secret

### 2. Get Google Refresh Token

```bash
# Install Google Auth library
pip install google-auth-oauthlib

# Run this Python script to get refresh token
python scripts/get_google_token.py
```

### 3. Create Google Drive Structure

Create these folders in your Google Drive:
- `vivian-hsa/` (root folder)
  - `reimbursed_receipts/`
  - `unreimbursed_receipts/`
  - `not_hsa_eligible_receipts/`

Get the folder IDs from the URLs and save them.

### 4. Create Google Sheet

1. Create a new Google Sheet
2. Add a sheet named `HSA_Ledger`
3. Add these headers in row 1:
   - id, provider, service_date, paid_date, amount, hsa_eligible, status, reimbursement_date, drive_file_id, confidence, created_at
4. Share the sheet with your service account email
5. Get the spreadsheet ID from the URL

### 5. Environment Configuration

```bash
# Copy example env files
cp apps/api/.env.example apps/api/.env
cp apps/mcp-server/.env.example apps/mcp-server/.env

# Edit both files with your credentials
```

### 6. Install Dependencies

```bash
# Create virtual environments
python -m venv venv
cd apps/api && python -m venv venv
cd ../mcp-server && python -m venv venv

# Install dependencies (in each venv)
pip install -e apps/api
pip install -e apps/mcp-server
```

## Running

### Development

```bash
# Terminal 1: Start API
cd apps/api
source venv/bin/activate
python -m vivian_api.main

# Terminal 2: Test endpoints
curl http://localhost:8000/health
```

### Running Tests

Tests are organized by app. Currently, MCP server tests are configured.

**Option 1: Full path (no activation needed)**
```bash
apps/test-mcp-server/venv/bin/pytest apps/test-mcp-server/tests/ -v
```

**Option 2: Activate venv manually**
```bash
cd apps/test-mcp-server
source venv/bin/activate
pytest tests/ -v
```

**Option 3: Using .envrc (Recommended)**
```bash
# First time only: set up venv
cd apps/test-mcp-server
python3 -m venv venv
source venv/bin/activate
pip install -e ".[test]"

# Then from project root, use the helper
source .envrc
test-mcp  # Runs only MCP tests
# or
pytest  # Runs all tests
```

### Database Migrations (Alembic)

```bash
cd apps/api
alembic -c alembic.ini upgrade head
```

Create a new migration after model changes:

```bash
cd apps/api
alembic -c alembic.ini revision --autogenerate -m "your change"
alembic -c alembic.ini upgrade head
```

For a quick walkthrough of models/repositories/migrations, see:
`docs/DATABASE_TOUR.md`

### Seed Identity Data (Home + Users + Memberships)

Use the seed utility to create or update a test home and related users.
The script uses `find_or_create` behavior for users/memberships.
Each run always creates a new `home` row.

Recommended (uses API container environment):

```bash
seed-id \
  --home-name "Demo Home" \
  --timezone "America/Chicago" \
  --client "owner@example.com:owner:default" \
  --client "parent@example.com:parent" \
  --client "child@example.com:child" \
  --password "owner@example.com:ChangeMe123!"
```

Equivalent direct command (without alias):

```bash
docker compose exec -T api python scripts/seed_identity.py \
  --home-name "Demo Home" \
  --timezone "America/Chicago" \
  --client "owner@example.com:owner:default" \
  --client "parent@example.com:parent" \
  --client "child@example.com:child" \
  --password "owner@example.com:ChangeMe123!"
```

Auto-create one member for each role:

```bash
seed-id \
  --home-name "Demo Home" \
  --timezone "America/Chicago" \
  --seed-all-roles \
  --default-email-domain "demo.local"
```

Notes:
- Membership roles: `owner`, `parent`, `child`, `caretaker`, `guest`, `member`.
- `--client` format is `email:role[:default]`.
- If you omit `--client`, the script auto-seeds one user for each role.
- `--seed-all-roles` can be combined with `--client`; duplicate email+role pairs are deduped.
- Home names are not deduped; duplicate names are allowed.
- Seeded memberships are marked `is_default_home=true` for all seeded users.
- `--password` is only applied for role `owner`; non-owner roles are always saved with empty password hash.
- Hash format uses PBKDF2-SHA256 with per-password random salt.

### Authentication Configuration

Set these API env vars (`apps/api/.env`):

- `VIVIAN_API_AUTH_JWT_SECRET`: JWT signing secret (required outside local dev)
- `VIVIAN_API_AUTH_JWT_ALGORITHM`: JWT algorithm (default `HS256`)
- `VIVIAN_API_AUTH_ACCESS_TOKEN_MINUTES`: access token TTL (default `15`)
- `VIVIAN_API_AUTH_REFRESH_TOKEN_DAYS`: refresh session TTL (default `30`)

### Authentication Flow

Auth endpoints are under `/api/v1/auth`:

- `POST /auth/login` with `{ email, password }` returns `{ access_token, refresh_token }`
- `POST /auth/refresh` with `{ refresh_token }` rotates the refresh token and returns a new pair
- `POST /auth/logout` with `{ refresh_token }` revokes the active refresh session
- `GET /auth/me` with bearer access token returns user + default home + memberships

Session persistence:

- Refresh sessions are stored in `auth_sessions` with hashed refresh token, expiry, and optional user-agent/IP.
- Refresh rotation revokes prior refresh token record.
- Protected routes now include these prefixes: `/api/v1/receipts/*`, `/api/v1/ledger/*`, `/api/v1/mcp/*`, `/api/v1/integrations/*`, `/api/v1/chat/*` (HTTP), and `/api/v1/chats/*`.
- `owner`/`parent` is required for MCP enabled-server updates (`POST /api/v1/mcp/servers/enabled`).

### Using Docker

```bash
docker-compose up -d
```

## API Endpoints

### Auth

- `POST /api/v1/auth/login`
- `POST /api/v1/auth/refresh`
- `POST /api/v1/auth/logout`
- `GET /api/v1/auth/me`

### Receipts

- `POST /api/v1/receipts/upload` - Upload PDF receipt
- `POST /api/v1/receipts/parse` - Parse uploaded receipt with OpenRouter
- `POST /api/v1/receipts/confirm` - Confirm and save to Drive + Ledger
- `POST /api/v1/receipts/bulk-import` - Bulk import directory of PDFs

### Ledger

- `GET /api/v1/ledger/balance/unreimbursed` - Get total unreimbursed amount

## Workflow

1. **Upload**: Client uploads PDF to `/upload` → gets temp path
2. **Parse**: Client sends temp path to `/parse` → gets extracted data + confidence
3. **Review**: Frontend shows data, allows editing
4. **Confirm**: Client sends edited data + status choice to `/confirm`
5. **Save**: API uploads to Drive, adds to Sheets, returns entry ID

## Human-in-the-Loop

After parsing, if confidence < 0.85, the system flags the receipt for review. The frontend should:
- Show extracted fields
- Allow inline editing
- Ask: "Did you already reimburse this from your HSA?"
  - Already reimbursed → status: `reimbursed`
  - Save for future → status: `unreimbursed`
  - Not HSA eligible → status: `not_hsa_eligible`

## Project Structure

```
├── apps/
│   ├── api/                    # FastAPI backend
│   │   ├── src/
│   │   │   ├── main.py
│   │   │   ├── routers/
│   │   │   │   ├── receipts.py
│   │   │   │   └── ledger.py
│   │   │   └── services/
│   │   │       ├── receipt_parser.py   # OpenRouter integration
│   │   │       └── mcp_client.py       # MCP communication
│   │   └── .env.example
│   └── mcp-server/             # MCP server
│       ├── src/
│       │   ├── server.py
│       │   └── tools/
│       │       ├── hsa_tools.py        # Ledger operations
│       │       └── drive_tools.py      # Drive operations
│       └── .env.example
├── packages/
│   └── shared/                 # Shared models
│       └── src/models.py
└── docker-compose.yml
```

## Security Notes

- Never commit `.env` files
- Store refresh tokens securely
- Use HTTPS in production
- Consider adding authentication for API endpoints

## Future Enhancements

- [ ] Local LLM support (Ollama integration)
- [ ] Model router for switching local/cloud
- [ ] Additional tool groups (finance, maintenance, etc.)
- [ ] Calendar integration
- [ ] Notification system
