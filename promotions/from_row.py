"""Map a persisted promotion row to a :class:`~promotions.models.Promotion`."""

from decimal import Decimal

from .models import Promotion


def _decimal(value):
    return None if value is None else Decimal(str(value))


def _bool(value):
    return None if value is None else bool(value)


def _denominations(row):
    if "reward_denominations" not in row.keys():
        return ()
    raw = row["reward_denominations"] or ""
    values = []
    for part in str(raw).split(","):
        part = part.strip()
        if part:
            try:
                values.append(Decimal(part))
            except Exception:  # noqa: BLE001
                continue
    return tuple(values)


def promotion_from_row(row) -> Promotion:
    def optional(name):
        return row[name] if name in row.keys() else None

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
        reward_unit_amount=_decimal(optional("reward_unit_amount")),
        reward_denominations=_denominations(row),
        reward_expiry_hours=optional("reward_expiry_hours"),
        reward_issue_after_settlement=(
            _bool(optional("reward_issue_after_settlement"))
            if "reward_issue_after_settlement" in row.keys()
            else None
        ),
        reward_restrictions=tuple(
            part for part in (str(optional("reward_restrictions") or "").split("|")) if part
        ),
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
        workflow_state=(
            (row["workflow_state"] if "workflow_state" in row.keys() else None) or "DRAFT"
        ),
        eligible_markets=tuple(
            part for part in (
                (row["eligible_markets"] if "eligible_markets" in row.keys() else "") or ""
            ).split(",") if part
        ),
        pre_match_only=(
            _bool(row["pre_match_only"]) if "pre_match_only" in row.keys() else None
        ),
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
