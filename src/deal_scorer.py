"""Deal scoring logic."""

import logging
from dataclasses import dataclass

from src.ebay_client import RawListing
from src.listing_cleaner import Classification

logger = logging.getLogger(__name__)


@dataclass
class DealResult:
    score: float
    discount_percent: float
    should_alert: bool
    reason: str


def calculate_deal_score(
    listing: RawListing,
    target_market_price: float | None,
    min_discount_percent: float,
    classification: Classification,
    target_grade: str | None = None,
) -> DealResult:
    """
    Score a listing and decide whether to fire an alert.

    Args:
        listing: Normalised listing from EbayClient.
        target_market_price: The price we consider fair market value.
        min_discount_percent: Minimum discount required to alert.
        classification: Output of clean_and_classify_listing().
        target_grade: Grade string from Watchlist, used for bonus scoring.

    Returns:
        DealResult with score (0–100), discount %, and alert flag.
    """

    # --- hard exits -----------------------------------------------------------
    if not target_market_price or target_market_price <= 0:
        return DealResult(
            score=0,
            discount_percent=0,
            should_alert=False,
            reason="no target_market_price configured",
        )

    if listing.total_price >= target_market_price:
        return DealResult(
            score=0,
            discount_percent=0,
            should_alert=False,
            reason=f"price {listing.total_price} >= market {target_market_price}",
        )

    if classification.is_bad_match:
        return DealResult(
            score=0,
            discount_percent=0,
            should_alert=False,
            reason=f"bad match: {', '.join(classification.reasons)}",
        )

    discount_percent = (
        (target_market_price - listing.total_price) / target_market_price
    ) * 100

    if discount_percent < min_discount_percent:
        return DealResult(
            score=0,
            discount_percent=round(discount_percent, 2),
            should_alert=False,
            reason=f"discount {discount_percent:.1f}% < minimum {min_discount_percent}%",
        )

    # --- score calculation ----------------------------------------------------
    base_score = min(100.0, discount_percent * 4)

    bonus = 0.0
    penalty = 0.0

    # Bonus: exact grade match
    if target_grade and classification.is_graded:
        tg_norm = target_grade.upper().strip()
        found_grade = f"{classification.grading_company} {classification.grade}"
        if found_grade == tg_norm.replace(" ", " "):  # normalise spacing
            bonus += 10

    # Special PSA 10 bonus
    if target_grade and "PSA 10" in target_grade.upper() and classification.is_psa10:
        bonus += 10

    # Penalty: risky wording
    if classification.is_risky:
        penalty += 20

    raw_score = base_score + bonus - penalty
    score = round(max(0.0, min(100.0, raw_score)), 2)

    should_alert = score >= 70 and discount_percent >= min_discount_percent

    reason = (
        f"discount={discount_percent:.1f}% score={score}"
        if should_alert
        else f"score {score} < 70 threshold"
    )

    return DealResult(
        score=score,
        discount_percent=round(discount_percent, 2),
        should_alert=should_alert,
        reason=reason,
    )
