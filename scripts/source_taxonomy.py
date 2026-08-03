"""
source_taxonomy.py
Shared RSS source category definitions.
"""

SOURCE_CATEGORIES = {
    "wire",
    "public_broadcaster",
    "newspaper",
    "digital_native",
    "nonprofit_investigative",
    "tv",
    "radio",
    "regional",
}

CATEGORY_LABELS = {
    "wire": "Wire service",
    "public_broadcaster": "Public broadcaster",
    "newspaper": "Newspaper",
    "digital_native": "Digital-native outlet",
    "nonprofit_investigative": "Nonprofit investigative outlet",
    "tv": "Television outlet",
    "radio": "Radio outlet",
    "regional": "Regional/local outlet",
}


def normalize_category(value: str | None) -> str:
    return (value or "").strip().lower().replace("-", "_").replace(" ", "_")


def is_valid_category(value: str | None) -> bool:
    return normalize_category(value) in SOURCE_CATEGORIES
