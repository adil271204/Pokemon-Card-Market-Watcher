"""Clean and classify a raw eBay listing title."""

import re
from dataclasses import dataclass, field


# Keywords that make a listing immediately undesirable
_BAD_KEYWORDS = {
    "proxy",
    "reprint",
    "custom",
    "orica",
    "metal",
    "jumbo",
    "digital",
    "lot",
    "bundle",
    "collection",
}

# Grade patterns we recognise
_GRADE_PATTERN = re.compile(
    r"\b(PSA|BGS|CGC|SGC)\s*(\d+(?:\.\d+)?)\b",
    re.IGNORECASE,
)

_RISKY_PHRASES = re.compile(
    r"PSA\s*10\s*\?|PSA\s*10\s*candidate|potential\s*PSA\s*10",
    re.IGNORECASE,
)


@dataclass
class Classification:
    is_proxy: bool = False
    is_reprint: bool = False
    is_custom: bool = False
    is_lot: bool = False
    is_bundle: bool = False
    is_digital: bool = False
    is_graded: bool = False
    grading_company: str | None = None
    grade: str | None = None
    is_psa10: bool = False
    is_bad_match: bool = False
    is_risky: bool = False
    reasons: list[str] = field(default_factory=list)


def clean_and_classify_listing(
    title: str,
    target_grade: str | None = None,
) -> Classification:
    """
    Analyse *title* and return a Classification.

    Args:
        title: Raw eBay listing title.
        target_grade: Grade string from the Watchlist, e.g. ``"PSA 10"``.

    Returns:
        A Classification dataclass with all flags and a *reasons* list
        explaining why the listing was flagged as bad.
    """
    cl = Classification()
    lower = title.lower()
    words = set(re.findall(r"\w+", lower))

    # --- bad-keyword detection -----------------------------------------------
    for kw in _BAD_KEYWORDS:
        if kw in words:
            setattr(cl, f"is_{kw}" if hasattr(cl, f"is_{kw}") else "_", True)
            # Map to the right field name
            field_map = {
                "proxy": "is_proxy",
                "reprint": "is_reprint",
                "custom": "is_custom",
                "lot": "is_lot",
                "bundle": "is_bundle",
                "digital": "is_digital",
            }
            if kw in field_map:
                setattr(cl, field_map[kw], True)
            cl.is_bad_match = True
            cl.reasons.append(f"bad keyword: '{kw}'")

    # --- grade detection ------------------------------------------------------
    match = _GRADE_PATTERN.search(title)
    if match:
        cl.is_graded = True
        cl.grading_company = match.group(1).upper()
        cl.grade = match.group(2)
        cl.is_psa10 = cl.grading_company == "PSA" and cl.grade == "10"

    # --- risky phrase check ---------------------------------------------------
    if _RISKY_PHRASES.search(title):
        cl.is_risky = True
        cl.reasons.append("risky phrase (e.g. 'PSA 10?')")

    # --- target-grade enforcement ---------------------------------------------
    if target_grade:
        tg = target_grade.upper().strip()

        if tg == "PSA 10":
            if not cl.is_psa10:
                cl.is_bad_match = True
                if cl.is_graded:
                    cl.reasons.append(
                        f"grade mismatch: found {cl.grading_company} {cl.grade}, need PSA 10"
                    )
                else:
                    cl.reasons.append("no PSA 10 grade found in title")
            # Listings graded by a different company also fail PSA-10 check
            if cl.is_graded and cl.grading_company not in ("PSA",):
                cl.is_bad_match = True
                cl.reasons.append(
                    f"wrong grading company: {cl.grading_company} (need PSA)"
                )

        elif tg.startswith("PSA"):
            required_grade = tg.replace("PSA", "").strip()
            if not (cl.is_graded and cl.grading_company == "PSA" and cl.grade == required_grade):
                cl.is_bad_match = True
                found = (
                    f"{cl.grading_company} {cl.grade}" if cl.is_graded else "ungraded"
                )
                cl.reasons.append(f"grade mismatch: found {found}, need {tg}")

    return cl
