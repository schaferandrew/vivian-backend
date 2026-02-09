"""Date parsing and comparison helpers."""

from datetime import datetime, timedelta
from typing import Optional, Tuple


# Common date formats to try when parsing
COMMON_DATE_FORMATS = [
    "%Y-%m-%d",      # ISO format: 2024-01-15
    "%Y/%m/%d",      # Slash format: 2024/01/15
    "%m/%d/%Y",      # US format: 01/15/2024
    "%m-%d-%Y",      # US hyphen: 01-15-2024
    "%d/%m/%Y",      # EU format: 15/01/2024
    "%d-%m-%Y",      # EU hyphen: 15-01-2024
    "%b %d, %Y",     # Text month: Jan 15, 2024
    "%B %d, %Y",     # Full month: January 15, 2024
    "%d %b %Y",      # EU text: 15 Jan 2024
    "%Y%m%d",        # Compact: 20240115
]


def parse_date(date_str: str, formats: Optional[list[str]] = None) -> Optional[datetime]:
    """Parse date string in various formats.
    
    Args:
        date_str: Date string to parse
        formats: Optional list of format strings to try (defaults to COMMON_DATE_FORMATS)
        
    Returns:
        Parsed datetime or None if parsing fails
        
    Example:
        >>> parse_date("2024-01-15")
        datetime.datetime(2024, 1, 15, 0, 0)
        >>> parse_date("01/15/2024")
        datetime.datetime(2024, 1, 15, 0, 0)
        >>> parse_date("invalid")
        None
    """
    if not date_str:
        return None
    
    formats_to_try = formats or COMMON_DATE_FORMATS
    
    for fmt in formats_to_try:
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except ValueError:
            continue
    
    return None


def days_between(date1: str, date2: str) -> Optional[int]:
    """Calculate absolute days between two date strings.
    
    Args:
        date1: First date string
        date2: Second date string
        
    Returns:
        Absolute number of days difference, or None if either date can't be parsed
        
    Example:
        >>> days_between("2024-01-15", "2024-01-18")
        3
        >>> days_between("2024-01-18", "2024-01-15")
        3
    """
    d1 = parse_date(date1)
    d2 = parse_date(date2)
    
    if not d1 or not d2:
        return None
    
    return abs((d2 - d1).days)


def is_within_days(date1: str, date2: str, days: int) -> bool:
    """Check if two dates are within N days of each other.
    
    Args:
        date1: First date string
        date2: Second date string
        days: Number of days tolerance
        
    Returns:
        True if dates are within the specified days of each other
        
    Example:
        >>> is_within_days("2024-01-15", "2024-01-17", 3)
        True
        >>> is_within_days("2024-01-15", "2024-01-20", 3)
        False
    """
    diff = days_between(date1, date2)
    if diff is None:
        return False
    return diff <= days


def get_date_range(
    center_date: str, 
    days_before: int = 3, 
    days_after: int = 3
) -> Tuple[Optional[datetime], Optional[datetime]]:
    """Get date range around a center date.
    
    Args:
        center_date: Center date string
        days_before: Days before center date
        days_after: Days after center date
        
    Returns:
        Tuple of (start_date, end_date) or (None, None) if parsing fails
        
    Example:
        >>> get_date_range("2024-01-15", days_before=2, days_after=2)
        (datetime(2024, 1, 13), datetime(2024, 1, 17))
    """
    center = parse_date(center_date)
    if not center:
        return None, None
    
    start = center - timedelta(days=days_before)
    end = center + timedelta(days=days_after)
    
    return start, end
