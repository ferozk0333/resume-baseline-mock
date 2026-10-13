"""Claim-name synonyms applied when saving (name_norm).

Keep this list small: obvious spelling variants only. Abbreviations and
related-but-different terms do not belong here; baseline aliases are the main
matching mechanism and are applied at judge time.
Keys and values are already normalized (core.text.normalize_name).
"""

CLAIM_SYNONYMS: dict[str, str] = {
    "sklearn": "scikit learn",   # scikit-learn
    "python3": "python",
    "nodejs": "node js",         # Node.js
}
