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

### Using Docker

```bash
docker-compose up -d
```

## API Endpoints

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
