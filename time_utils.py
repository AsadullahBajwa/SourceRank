"""Small UTC helpers that keep SourceRank's existing naive-UTC storage format."""

import datetime


UTC = datetime.timezone.utc


def utc_now() -> datetime.datetime:
    """Return the current time as a naive UTC datetime for DB compatibility."""
    return datetime.datetime.now(UTC).replace(tzinfo=None)


def utc_now_iso() -> str:
    """Return the current UTC timestamp in the repo's existing ISO format."""
    return utc_now().isoformat()


def parse_utc(value: str) -> datetime.datetime:
    """Parse ISO timestamps with or without timezone data into naive UTC."""
    dt = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is not None:
        return dt.astimezone(UTC).replace(tzinfo=None)
    return dt
