"""Map a persisted promotion row to a :class:`~promotions.models.Promotion`."""

from decimal import Decimal

from .models import Promotion


def _decimal(value):
    return None if value is None else Decimal(str(value))


def _bool(value):
    return None if value is None else bool(value)


def promotion_from_row(row) -> Promotion:
    return Promotion(
        id=row["id"],
        sportsbook=row["sportsbook"] or "",
        name=row["name"] or "",
        jurisdiction=row["jurisdiction"],
        offer_type=row["offer_type"],
        new_customer_only=_bool(row["new_customer_only"]),
        qualifying_stake=_decimal(row["qualifying_stake"]),
        qualifying_min_odds=_decimal(row["qualifying_min_odds"]),
        qualifying_max_odds=_decimal(row["qualifying_max_odds"]),
        reward_amount=_decimal(row["reward_amount"]),
        reward_type=row["reward_type"],
        reward_count=row["reward_count"] or 1,
        stake_returned=_bool(row["stake_returned"]),
        reward_expiry_days=row["reward_expiry_days"],
        source_url=row["source_url"],
        terms_text=row["terms_text"],
        status=row["status"],
        confidence=row["confidence"] or 0.0,
        eligibility=row["eligibility"] or "Unknown",
        official_source_url=row["official_source_url"] if "official_source_url" in row.keys() else None,
        terms_source_url=row["terms_source_url"] if "terms_source_url" in row.keys() else None,
        effective_date=row["effective_date"] if "effective_date" in row.keys() else None,
        lifecycle_status=(
            row["lifecycle_status"] if "lifecycle_status" in row.keys() else None
        ) or row["status"],
        withdrawal_restrictions=(
            row["withdrawal_restrictions"] if "withdrawal_restrictions" in row.keys() else None
        ),
        wagering_requirement_text=(
            row["wagering_requirement_text"] if "wagering_requirement_text" in row.keys() else None
        ),
        min_deposit=_decimal(row["min_deposit"]) if "min_deposit" in row.keys() else None,
        max_qualifying_stake=(
            _decimal(row["max_qualifying_stake"]) if "max_qualifying_stake" in row.keys() else None
        ),
    )
