"""Text normalization helpers for consistent string processing."""

import re


def normalize_provider(provider: str) -> str:
    """Normalize provider name for comparison.
    
    Args:
        provider: Raw provider name
        
    Returns:
        Normalized provider name (lowercase, stripped, suffixes removed)
        
    Example:
        >>> normalize_provider("Dr. Smith MD")
        'dr. smith'
        >>> normalize_provider("ACME Medical LLC")
        'acme medical'
    """
    if not provider:
        return ""
    
    # Lowercase and strip
    normalized = provider.lower().strip()
    
    # Remove common business/professional suffixes
    suffixes = [
        r'\s+llc\.?$',
        r'\s+inc\.?$',
        r'\s+corp\.?$',
        r'\s+co\.?$',
        r'\s+ltd\.?$',
        r'\s+md\.?$',
        r'\s+do\.?$',
        r'\s+dds\.?$',
        r'\s+dmd\.?$',
        r'\s+phd\.?$',
        r'\s+np\.?$',
        r'\s+pa\.?$',
        r'\s+rn\.?$',
    ]
    
    for suffix in suffixes:
        normalized = re.sub(suffix, '', normalized)
    
    # Remove extra whitespace
    normalized = ' '.join(normalized.split())
    
    return normalized


def normalize_title(value: str) -> str:
    """Normalize title for loose matching.
    
    Removes spaces, underscores, and converts to lowercase.
    
    Args:
        value: Title string to normalize
        
    Returns:
        Normalized title
        
    Example:
        >>> normalize_title("HSA_Ledger")
        'hsaledger'
        >>> normalize_title("HSA Ledger")
        'hsaledger'
    """
    return value.strip().lower().replace(" ", "").replace("_", "")


def normalize_header(value: str) -> str:
    """Normalize header cell values for comparison.
    
    Converts to lowercase, replaces spaces and hyphens with underscores.
    
    Args:
        value: Header string to normalize
        
    Returns:
        Normalized header
        
    Example:
        >>> normalize_header("Service Date")
        'service_date'
        >>> normalize_header("service-date")
        'service_date'
    """
    return value.strip().lower().replace(" ", "_").replace("-", "_")


def escape_sheet_title(sheet_title: str) -> str:
    """Escape worksheet title for Google Sheets A1 notation.
    
    Single quotes within sheet titles must be doubled.
    
    Args:
        sheet_title: Worksheet title
        
    Returns:
        Escaped title safe for use in range notation
        
    Example:
        >>> escape_sheet_title("Sheet's Data")
        "Sheet''s Data"
    """
    return sheet_title.replace("'", "''")
