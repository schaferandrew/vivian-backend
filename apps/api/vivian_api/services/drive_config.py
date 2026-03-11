"""Drive structure bootstrap and per-home config cache.

On first use, `get_or_create_config` checks Drive for an existing `.config`
sheet under a `Vivian/` root folder.  If the sheet is missing, the full
folder/sheet hierarchy is created and the IDs are written to `.config`.

Subsequent calls return from the in-process cache (IDs never change).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from vivian_api.services.google_sheets import (
    build_google_credentials,
    create_folder,
    create_sheet,
    get_drive_service,
    get_sheets_service,
    search_drive,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from vivian_api.config import Settings

logger = logging.getLogger(__name__)

# In-process cache — keyed by home_id.  IDs are permanent, so no TTL needed.
_config_cache: dict[str, dict] = {}

_HSA_HEADERS = [
    "id",
    "provider",
    "service_date",
    "paid_date",
    "amount",
    "hsa_eligible",
    "status",
    "reimbursement_date",
    "drive_file_id",
    "confidence",
    "created_at",
]

_DONATIONS_HEADERS = [
    "id",
    "organization_name",
    "donation_date",
    "amount",
    "tax_deductible",
    "description",
    "drive_file_id",
    "tax_year",
    "confidence",
    "created_at",
]

_REQUIRED_KEYS = [
    "hsa_folder_id",
    "hsa_receipts_folder_id",
    "hsa_ledger_id",
    "donations_folder_id",
    "donations_receipts_folder_id",
    "donations_ledger_id",
]


async def get_or_create_config(home_id: str, db: "Session", settings: "Settings") -> dict:
    """Return cached Drive config for home, bootstrapping if needed.

    Returns a dict with keys:
        hsa_folder_id, hsa_receipts_folder_id, hsa_ledger_id,
        donations_folder_id, donations_receipts_folder_id, donations_ledger_id
    """
    if home_id in _config_cache:
        return _config_cache[home_id]

    creds = build_google_credentials(home_id, db, settings)
    drive_svc = get_drive_service(creds)
    sheets_svc = get_sheets_service(creds)

    config = _read_config_sheet(drive_svc, sheets_svc)
    if config is None:
        config = _bootstrap_drive_structure(drive_svc, sheets_svc)

    _config_cache[home_id] = config
    return config


def _read_config_sheet(drive_svc, sheets_svc) -> dict | None:
    """Try to read IDs from the existing .config sheet. Returns None if absent."""
    vivian_id = search_drive(drive_svc, "Vivian")
    if not vivian_id:
        return None

    config_id = search_drive(drive_svc, ".config", parent_id=vivian_id)
    if not config_id:
        return None

    result = (
        sheets_svc.spreadsheets()
        .values()
        .get(spreadsheetId=config_id, range="Sheet1!A:B")
        .execute()
    )
    config: dict[str, str] = {}
    for row in result.get("values", []):
        if len(row) >= 2:
            config[row[0]] = row[1]

    return config if all(k in config for k in _REQUIRED_KEYS) else None


def _bootstrap_drive_structure(drive_svc, sheets_svc) -> dict:
    """Create the Vivian/ folder hierarchy and return the resulting config."""
    logger.info("Bootstrapping Vivian Drive structure")

    vivian_id = search_drive(drive_svc, "Vivian") or create_folder(drive_svc, "Vivian")

    # HSA branch
    hsa_id = search_drive(drive_svc, "HSA", parent_id=vivian_id) or create_folder(
        drive_svc, "HSA", parent_id=vivian_id
    )
    hsa_receipts_id = search_drive(
        drive_svc, "Receipts", parent_id=hsa_id
    ) or create_folder(drive_svc, "Receipts", parent_id=hsa_id)
    hsa_ledger_id = search_drive(
        drive_svc, "Ledger", parent_id=hsa_id
    ) or create_sheet(drive_svc, sheets_svc, "Ledger", hsa_id, _HSA_HEADERS)

    # Donations branch
    donations_id = search_drive(
        drive_svc, "Donations", parent_id=vivian_id
    ) or create_folder(drive_svc, "Donations", parent_id=vivian_id)
    donations_receipts_id = search_drive(
        drive_svc, "Receipts", parent_id=donations_id
    ) or create_folder(drive_svc, "Receipts", parent_id=donations_id)
    donations_ledger_id = search_drive(
        drive_svc, "Ledger", parent_id=donations_id
    ) or create_sheet(
        drive_svc, sheets_svc, "Ledger", donations_id, _DONATIONS_HEADERS
    )

    config = {
        "hsa_folder_id": hsa_id,
        "hsa_receipts_folder_id": hsa_receipts_id,
        "hsa_ledger_id": hsa_ledger_id,
        "donations_folder_id": donations_id,
        "donations_receipts_folder_id": donations_receipts_id,
        "donations_ledger_id": donations_ledger_id,
    }
    _write_config_sheet(drive_svc, sheets_svc, vivian_id, config)
    logger.info("Drive bootstrap complete: %s", config)
    return config


def _write_config_sheet(drive_svc, sheets_svc, vivian_id: str, config: dict) -> None:
    """Create the .config sheet and write key/value pairs starting at row 2."""
    config_id = create_sheet(
        drive_svc, sheets_svc, ".config", vivian_id, ["key", "value"]
    )
    rows = [[k, v] for k, v in config.items()]
    sheets_svc.spreadsheets().values().update(
        spreadsheetId=config_id,
        range="Sheet1!A2",
        valueInputOption="USER_ENTERED",
        body={"values": rows},
    ).execute()
