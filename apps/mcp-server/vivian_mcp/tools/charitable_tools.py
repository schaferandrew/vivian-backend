"""Charitable donation tools for MCP server."""

import json
import uuid
from datetime import datetime
from typing import Any

from vivian_mcp.config import Settings
from vivian_mcp.tools.google_common import (
    GoogleServiceMixin,
    DriveOperationsMixin,
    SheetsOperationsMixin,
    apply_column_filters,
)


class CharitableToolManager(GoogleServiceMixin, DriveOperationsMixin, SheetsOperationsMixin):
    """Manages charitable donation tracking operations."""

    # Expected headers for charitable ledger
    EXPECTED_HEADERS = [
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

    CASH_DONATION_DRIVE_FILE_ID = "cash_donation_no_receipt"

    def __init__(self):
        super().__init__(Settings())

    def _resolve_spreadsheet(self) -> tuple[str, str]:
        """Return (spreadsheet_id, worksheet_name) from settings."""
        spreadsheet_id = self.settings.charitable_spreadsheet_id or self.settings.hsa_spreadsheet_id
        worksheet_name = self.settings.charitable_worksheet_name or self.settings.hsa_worksheet_name or "Charitable Donations"
        return spreadsheet_id, worksheet_name

    def _get_tax_year(self, donation_date: str) -> str:
        """Extract tax year from donation date."""
        try:
            # Try to parse the date and extract year
            date_formats = [
                "%Y-%m-%d",
                "%Y/%m/%d",
                "%m/%d/%Y",
                "%m-%d-%Y",
                "%d/%m/%Y",
                "%d-%m-%Y",
                "%b %d, %Y",
                "%B %d, %Y",
            ]
            
            for fmt in date_formats:
                try:
                    parsed_date = datetime.strptime(donation_date.strip(), fmt)
                    return str(parsed_date.year)
                except ValueError:
                    continue
        except Exception:
            pass
        
        # Fallback to current year
        return str(datetime.now().year)

    async def upload_receipt_to_drive(
        self,
        local_file_path: str,
        tax_year: str = None,
        filename: str = None
    ) -> str:
        """Upload receipt to Google Drive.
        
        Args:
            local_file_path: Path to local receipt file
            tax_year: Optional tax year for folder organization (e.g., "2025")
            filename: Optional custom filename
            
        Returns:
            JSON string with success, file_id, error
        """
        try:
            # Use charitable folder, fall back to root
            drive_folder_id = self.settings.charitable_drive_folder_id or self.settings.drive_root_folder_id
            if not drive_folder_id:
                return json.dumps({
                    "success": False,
                    "error": "No drive folder configured. Set charitable_drive_folder_id or drive_root_folder_id in settings."
                })
            folder_result = {"success": True, "folder_id": drive_folder_id}
            
            if not folder_result.get("success"):
                return json.dumps({
                    "success": False,
                    "error": f"Failed to get/create folder: {folder_result.get('error')}"
                })
            
            folder_id = folder_result["folder_id"]
            
            # Upload file
            upload_result = await self.upload_file(
                local_file_path=local_file_path,
                folder_id=folder_id,
                filename=filename,
                add_timestamp=True
            )
            
            if upload_result.get("success"):
                upload_result["folder_id"] = folder_id
                upload_result["tax_year"] = tax_year
            
            return json.dumps(upload_result)
            
        except Exception as e:
            return json.dumps({
                "success": False,
                "error": str(e)
            })

    async def append_donation_to_ledger(
        self,
        donation_json: dict,
        drive_file_id: str,
        check_duplicates: bool = True,
        force_append: bool = False,
    ) -> str:
        """Append donation to charitable ledger.
        
        Args:
            donation_json: Donation data (organization_name, donation_date, amount, etc.)
            drive_file_id: Google Drive file ID of uploaded receipt
            check_duplicates: Whether to check for duplicates
            force_append: Whether to force append even if duplicate found
            
        Returns:
            JSON string with success, entry_id, error
        """
        try:
            # Extract data
            organization_name = donation_json.get("organization_name", "Unknown")
            donation_date = donation_json.get("donation_date", "")
            amount = donation_json.get("amount", 0)
            tax_deductible = donation_json.get("tax_deductible", True)
            description = donation_json.get("description", "")
            confidence = donation_json.get("confidence", 0.9)
            
            # Calculate tax year
            tax_year = self._get_tax_year(donation_date)
            
            # Resolve spreadsheet and worksheet from settings
            spreadsheet_id, worksheet_name = self._resolve_spreadsheet()
            
            if not spreadsheet_id:
                return json.dumps({
                    "success": False,
                    "error": "No spreadsheet ID configured. Set charitable_spreadsheet_id in MCP server settings."
                })
            
            ensure_result = await self.ensure_worksheet_exists(
                spreadsheet_id=spreadsheet_id,
                worksheet_name=worksheet_name,
                headers=self.EXPECTED_HEADERS
            )
            
            if not ensure_result.get("success"):
                return json.dumps({
                    "success": False,
                    "error": f"Failed to ensure worksheet: {ensure_result.get('error')}"
                })
            
            # Check for duplicates if requested
            if check_duplicates and not force_append:
                duplicate_check = await self.check_for_duplicates(donation_json)
                
                if duplicate_check.get("is_duplicate"):
                    return json.dumps({
                        "success": False,
                        "error": "Duplicate donation detected",
                        "duplicate_check": duplicate_check,
                    })
            
            # Generate entry ID
            entry_id = str(uuid.uuid4())[:8]
            
            # Prepare row data
            row_data = [
                entry_id,
                organization_name,
                donation_date,
                amount,
                "Yes" if tax_deductible else "No",
                description,
                drive_file_id,
                tax_year,
                confidence,
                datetime.now().isoformat(),
            ]
            
            # Append to sheet
            append_result = await self.append_row(
                spreadsheet_id=spreadsheet_id,
                worksheet_name=worksheet_name,
                row_data=row_data
            )
            
            if not append_result.get("success"):
                return json.dumps({
                    "success": False,
                    "error": f"Failed to append to sheet: {append_result.get('error')}"
                })
            
            return json.dumps({
                "success": True,
                "entry_id": entry_id,
                "tax_year": tax_year,
            })
            
        except Exception as e:
            return json.dumps({
                "success": False,
                "error": str(e)
            })

    async def append_cash_donation_to_ledger(
        self,
        donation_json: dict,
        check_duplicates: bool = True,
        force_append: bool = False,
    ) -> str:
        """Append a cash donation directly to the charitable ledger.

        Cash donations do not have a Drive receipt upload, so this writes a
        sentinel Drive file id to keep the existing ledger schema unchanged.
        """

        donation_payload = dict(donation_json)
        description = str(donation_payload.get("description", "") or "").strip()
        if not description:
            donation_payload["description"] = "Cash donation"

        return await self.append_donation_to_ledger(
            donation_payload,
            self.CASH_DONATION_DRIVE_FILE_ID,
            check_duplicates=check_duplicates,
            force_append=force_append,
        )

    async def check_for_duplicates(
        self,
        donation_json: dict,
        fuzzy_days: int = 3
    ) -> dict:
        """Check for duplicate donations in the ledger.
        
        Args:
            donation_json: Donation data to check
            fuzzy_days: Number of days to allow for fuzzy date matching
            
        Returns:
            Dict with is_duplicate, potential_duplicates, recommendation
        """
        try:
            # Get all existing entries
            spreadsheet_id, worksheet_name = self._resolve_spreadsheet()
            rows_result = await self.get_all_rows(
                spreadsheet_id=spreadsheet_id,
                worksheet_name=worksheet_name
            )
            
            if not rows_result.get("success"):
                # If we can't read the sheet, assume not duplicate
                return {
                    "is_duplicate": False,
                    "potential_duplicates": [],
                    "recommendation": "import",
                }
            
            headers = rows_result.get("headers", [])
            rows = rows_result.get("rows", [])
            
            if not headers or not rows:
                return {
                    "is_duplicate": False,
                    "potential_duplicates": [],
                    "recommendation": "import",
                }
            
            # Find column indices
            try:
                org_idx = headers.index("organization_name")
                date_idx = headers.index("donation_date")
                amount_idx = headers.index("amount")
            except ValueError:
                # Headers don't match expected format
                return {
                    "is_duplicate": False,
                    "potential_duplicates": [],
                    "recommendation": "import",
                }
            
            # Extract data to check
            new_org = donation_json.get("organization_name", "").lower().strip()
            new_date = donation_json.get("donation_date", "")
            new_amount = float(donation_json.get("amount", 0))
            
            potential_duplicates = []
            
            for row in rows:
                if len(row) < max(org_idx, date_idx, amount_idx) + 1:
                    continue
                
                existing_org = row[org_idx].lower().strip()
                existing_date = row[date_idx]
                try:
                    existing_amount = float(row[amount_idx])
                except (ValueError, TypeError):
                    continue
                
                # Check for exact match on organization and amount
                if existing_org == new_org and abs(existing_amount - new_amount) < 0.01:
                    # Check date with fuzzy matching
                    date_match = False
                    
                    if existing_date == new_date:
                        date_match = True
                    else:
                        # Try to parse dates and check difference
                        try:
                            from vivian_mcp.tools.hsa_tools import parse_date, days_between
                            days_diff = days_between(existing_date, new_date)
                            if days_diff is not None and days_diff <= fuzzy_days:
                                date_match = True
                        except Exception:
                            pass
                    
                    if date_match:
                        potential_duplicates.append({
                            "organization": row[org_idx],
                            "date": existing_date,
                            "amount": existing_amount,
                            "match_type": "exact" if existing_date == new_date else "fuzzy_date",
                            "days_difference": 0 if existing_date == new_date else days_between(existing_date, new_date),
                        })
            
            is_duplicate = len(potential_duplicates) > 0
            
            if is_duplicate:
                recommendation = "review"
            else:
                recommendation = "import"
            
            return {
                "is_duplicate": is_duplicate,
                "potential_duplicates": potential_duplicates,
                "recommendation": recommendation,
            }
            
        except Exception as e:
            # If duplicate check fails, return empty result (allow import)
            return {
                "is_duplicate": False,
                "potential_duplicates": [],
                "recommendation": "import",
                "check_error": str(e),
            }

    async def read_donation_entries(
        self,
        tax_year: str | int | None = None,
        organization: str | None = None,
        tax_deductible: bool | None = None,
        limit: int = 1000,
        column_filters: list[dict[str, Any]] | None = None,
    ) -> str:
        """Read charitable donation ledger entries with optional filters.

        Args:
            tax_year: Optional tax year filter (e.g., "2025" or 2025)
            organization: Optional organization_name contains filter
            tax_deductible: Optional deductible flag filter
            limit: Maximum number of entries to return
            column_filters: Optional list of ANDed column-level filters

        Returns:
            JSON string with entries and summary totals.
        """
        try:
            spreadsheet_id, worksheet_name = self._resolve_spreadsheet()
            rows_result = await self.get_all_rows(
                spreadsheet_id=spreadsheet_id,
                worksheet_name=worksheet_name,
            )
            if not rows_result.get("success"):
                return json.dumps(
                    {
                        "success": False,
                        "error": rows_result.get("error", "Failed to read ledger"),
                    }
                )

            headers = rows_result.get("headers", [])
            rows = rows_result.get("rows", [])
            filter_result = apply_column_filters(
                headers=headers,
                rows=rows,
                column_filters=column_filters,
            )
            if not filter_result.get("success"):
                return json.dumps(
                    {
                        "success": False,
                        "error": filter_result.get("error", "Invalid column filters"),
                        "available_columns": filter_result.get("available_columns", []),
                    }
                )
            rows = filter_result.get("rows", [])

            if not headers:
                empty_summary = {
                    "total_entries": 0,
                    "total_amount": 0.0,
                    "tax_deductible_total": 0.0,
                    "non_deductible_total": 0.0,
                    "count_tax_deductible": 0,
                    "count_non_deductible": 0,
                    "by_organization": {},
                    "by_year": {},
                }
                return json.dumps(
                    {
                        "success": True,
                        "tax_year": str(tax_year).strip() if tax_year is not None else None,
                        "entries": [],
                        "summary": empty_summary,
                        "total": 0.0,
                        "tax_deductible_total": 0.0,
                        "by_organization": {},
                        "by_year": {},
                    }
                )

            header_map = {str(header).strip().lower(): idx for idx, header in enumerate(headers)}
            required_columns = ("organization_name", "amount")
            missing_columns = [col for col in required_columns if col not in header_map]
            if missing_columns:
                return json.dumps(
                    {
                        "success": False,
                        "error": f"Ledger missing required columns: {', '.join(missing_columns)}",
                        "available_columns": sorted(header_map.keys()),
                    }
                )

            def value_at(row: list[Any], column_name: str, default: Any = "") -> Any:
                idx = header_map.get(column_name)
                if idx is None or idx >= len(row):
                    return default
                return row[idx]

            def parse_amount(value: Any) -> float:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    return 0.0

            def parse_bool(value: Any) -> bool:
                normalized = str(value or "").strip().lower()
                return normalized in {"yes", "true", "1", "y"}

            normalized_tax_year = None
            if tax_year is not None and str(tax_year).strip():
                normalized_tax_year = str(tax_year).strip()

            normalized_org = organization.strip().lower() if isinstance(organization, str) and organization.strip() else None
            if not isinstance(limit, int) or limit <= 0:
                limit = 1000

            entries: list[dict[str, Any]] = []
            total_amount = 0.0
            deductible_total = 0.0
            non_deductible_total = 0.0
            count_deductible = 0
            count_non_deductible = 0
            by_organization: dict[str, dict[str, float | int]] = {}
            by_year: dict[str, dict[str, float | int]] = {}

            for row in rows:
                org_name = str(value_at(row, "organization_name", "") or "").strip()
                row_tax_year = str(value_at(row, "tax_year", "") or "").strip()
                row_donation_date = str(value_at(row, "donation_date", "") or "").strip()
                if not row_tax_year and row_donation_date:
                    row_tax_year = self._get_tax_year(row_donation_date)
                amount = parse_amount(value_at(row, "amount", 0))
                is_deductible = parse_bool(value_at(row, "tax_deductible", ""))

                if normalized_tax_year and row_tax_year != normalized_tax_year:
                    continue
                if normalized_org and normalized_org not in org_name.lower():
                    continue
                if isinstance(tax_deductible, bool) and is_deductible != tax_deductible:
                    continue

                entry = {
                    "id": str(value_at(row, "id", "") or ""),
                    "organization_name": org_name,
                    "donation_date": row_donation_date,
                    "amount": amount,
                    "tax_deductible": is_deductible,
                    "description": str(value_at(row, "description", "") or ""),
                    "drive_file_id": str(value_at(row, "drive_file_id", "") or ""),
                    "tax_year": row_tax_year,
                    "confidence": str(value_at(row, "confidence", "") or ""),
                    "created_at": str(value_at(row, "created_at", "") or ""),
                }
                entries.append(entry)

                total_amount += amount
                if is_deductible:
                    deductible_total += amount
                    count_deductible += 1
                else:
                    non_deductible_total += amount
                    count_non_deductible += 1

                if org_name not in by_organization:
                    by_organization[org_name] = {"total": 0.0, "count": 0}
                by_organization[org_name]["total"] += amount
                by_organization[org_name]["count"] += 1

                if row_tax_year not in by_year:
                    by_year[row_tax_year] = {"total": 0.0, "count": 0}
                by_year[row_tax_year]["total"] += amount
                by_year[row_tax_year]["count"] += 1

                if len(entries) >= limit:
                    break

            summary = {
                "total_entries": len(entries),
                "total_amount": round(total_amount, 2),
                "tax_deductible_total": round(deductible_total, 2),
                "non_deductible_total": round(non_deductible_total, 2),
                "count_tax_deductible": count_deductible,
                "count_non_deductible": count_non_deductible,
                "by_organization": by_organization,
                "by_year": by_year,
            }
            return json.dumps(
                {
                    "success": True,
                    "tax_year": normalized_tax_year,
                    "entries": entries,
                    "summary": summary,
                    "total": summary["total_amount"],
                    "tax_deductible_total": summary["tax_deductible_total"],
                    "by_organization": summary["by_organization"],
                    "by_year": summary["by_year"],
                }
            )
        except Exception as e:
            return json.dumps(
                {
                    "success": False,
                    "error": str(e),
                }
            )

    async def get_donation_summary(
        self,
        tax_year: str = None,
        column_filters: list[dict[str, Any]] | None = None,
    ) -> str:
        """Get summary of charitable donations.
        
        Args:
            tax_year: Optional tax year to filter by (e.g., "2025")
            column_filters: Optional list of column-level filters (ANDed)
            
        Returns:
            JSON string with total, tax_deductible_total, by_organization, error
        """
        try:
            # Get all entries
            spreadsheet_id, worksheet_name = self._resolve_spreadsheet()
            rows_result = await self.get_all_rows(
                spreadsheet_id=spreadsheet_id,
                worksheet_name=worksheet_name
            )
            
            if not rows_result.get("success"):
                return json.dumps({
                    "success": False,
                    "error": rows_result.get("error", "Failed to read ledger")
                })
            
            headers = rows_result.get("headers", [])
            rows = rows_result.get("rows", [])
            filter_result = apply_column_filters(
                headers=headers,
                rows=rows,
                column_filters=column_filters,
            )
            if not filter_result.get("success"):
                return json.dumps({
                    "success": False,
                    "error": filter_result.get("error", "Invalid column filters"),
                    "available_columns": filter_result.get("available_columns", []),
                })
            rows = filter_result.get("rows", [])
            
            if not headers or not rows:
                return json.dumps({
                    "success": True,
                    "total": 0,
                    "tax_deductible_total": 0,
                    "by_organization": {},
                    "by_year": {},
                })
            
            # Find column indices
            try:
                org_idx = headers.index("organization_name")
                amount_idx = headers.index("amount")
                tax_deductible_idx = headers.index("tax_deductible")
                tax_year_idx = headers.index("tax_year")
            except ValueError:
                return json.dumps({
                    "success": False,
                    "error": "Ledger headers don't match expected format"
                })
            
            # Calculate totals
            total = 0.0
            tax_deductible_total = 0.0
            by_organization = {}
            by_year = {}
            
            for row in rows:
                if len(row) < max(org_idx, amount_idx, tax_deductible_idx, tax_year_idx) + 1:
                    continue
                
                try:
                    amount = float(row[amount_idx])
                except (ValueError, TypeError):
                    continue
                
                org = row[org_idx]
                is_deductible = row[tax_deductible_idx].lower() in ("yes", "true", "1")
                year = row[tax_year_idx]
                
                # Filter by tax year if specified
                if tax_year and year != tax_year:
                    continue
                
                total += amount
                
                if is_deductible:
                    tax_deductible_total += amount
                
                # Track by organization
                if org not in by_organization:
                    by_organization[org] = {"total": 0.0, "count": 0}
                by_organization[org]["total"] += amount
                by_organization[org]["count"] += 1
                
                # Track by year
                if year not in by_year:
                    by_year[year] = {"total": 0.0, "count": 0}
                by_year[year]["total"] += amount
                by_year[year]["count"] += 1
            
            return json.dumps({
                "success": True,
                "tax_year": tax_year,
                "total": round(total, 2),
                "tax_deductible_total": round(tax_deductible_total, 2),
                "by_organization": by_organization,
                "by_year": by_year,
            })
            
        except Exception as e:
            return json.dumps({
                "success": False,
                "error": str(e)
            })
