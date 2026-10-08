"""Configurable opportunity alert rules.

Rules are evaluated in-process over discovered opportunities. Delivery is behind
:class:`Notifier` so email/push can be added later. De-duplication is by a stable
key per (rule, opportunity) so an unchanged opportunity is not re-alerted.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from opportunities import Opportunity

ZERO = Decimal("0")
HUNDRED = Decimal("100")


def _decimal(value):
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _get(row, key, default=None):
    try:
        return row[key]
    except (KeyError, IndexError):
        return default


@dataclass(frozen=True)
class AlertRule:
    id: Optional[int] = None
    name: str = "Rule"
    enabled: bool = True
    kind: Optional[str] = None
    max_qualifying_loss_pct: Optional[Decimal] = None
    max_back_lay_gap_pct: Optional[Decimal] = None
    min_back_odds: Optional[Decimal] = None
    max_required_liability: Optional[Decimal] = None
    max_required_capital: Optional[Decimal] = None
    bookmaker: Optional[str] = None
    sport: Optional[str] = None
    min_free_bet_conversion: Optional[Decimal] = None
    notes: Optional[str] = None

    @classmethod
    def from_row(cls, row) -> "AlertRule":
        return cls(
            id=_get(row, "id"),
            name=_get(row, "name") or "Rule",
            enabled=bool(_get(row, "enabled", 1)),
            kind=_get(row, "kind"),
            max_qualifying_loss_pct=_decimal(_get(row, "max_qualifying_loss_pct")),
            max_back_lay_gap_pct=_decimal(_get(row, "max_back_lay_gap_pct")),
            min_back_odds=_decimal(_get(row, "min_back_odds")),
            max_required_liability=_decimal(_get(row, "max_required_liability")),
            max_required_capital=_decimal(_get(row, "max_required_capital")),
            bookmaker=_get(row, "bookmaker"),
            sport=_get(row, "sport"),
            min_free_bet_conversion=_decimal(_get(row, "min_free_bet_conversion")),
            notes=_get(row, "notes"),
        )


@dataclass(frozen=True)
class Alert:
    rule_id: Optional[int]
    rule_name: str
    dedup_key: str
    event_id: str
    selection: str
    bookmaker: str
    exchange: str
    message: str
    opportunity: Opportunity


def back_lay_gap_pct(opportunity: Opportunity) -> Decimal:
    if opportunity.back_odds <= ZERO:
        return ZERO
    return (opportunity.lay_odds - opportunity.back_odds) / opportunity.back_odds * HUNDRED


def matches(rule: AlertRule, opportunity: Opportunity) -> bool:
    if not rule.enabled:
        return False
    if rule.kind is not None and opportunity.kind.value != rule.kind:
        return False
    if rule.bookmaker is not None and opportunity.book_provider != rule.bookmaker:
        return False
    if rule.sport is not None and opportunity.sport != rule.sport:
        return False
    if rule.min_back_odds is not None and opportunity.back_odds < rule.min_back_odds:
        return False
    if (
        rule.max_qualifying_loss_pct is not None
        and opportunity.qualifying_loss_pct > rule.max_qualifying_loss_pct
    ):
        return False
    if (
        rule.max_required_liability is not None
        and opportunity.lay_liability > rule.max_required_liability
    ):
        return False
    if (
        rule.max_required_capital is not None
        and opportunity.required_capital > rule.max_required_capital
    ):
        return False
    if (
        rule.max_back_lay_gap_pct is not None
        and back_lay_gap_pct(opportunity) > rule.max_back_lay_gap_pct
    ):
        return False
    if rule.min_free_bet_conversion is not None:
        if (
            opportunity.conversion_rate is None
            or opportunity.conversion_rate < rule.min_free_bet_conversion
        ):
            return False
    return True


def alert_key(rule: AlertRule, opportunity: Opportunity) -> str:
    rule_id = rule.id if rule.id is not None else rule.name
    return "|".join(
        [
            str(rule_id),
            opportunity.event_id,
            opportunity.book_provider,
            opportunity.exchange_provider,
            opportunity.selection,
        ]
    )


def build_alert(rule: AlertRule, opportunity: Opportunity) -> Alert:
    return Alert(
        rule_id=rule.id,
        rule_name=rule.name,
        dedup_key=alert_key(rule, opportunity),
        event_id=opportunity.event_id,
        selection=opportunity.selection,
        bookmaker=opportunity.book_provider,
        exchange=opportunity.exchange_provider,
        message=f"{rule.name}: {opportunity.event_label} — {opportunity.selection}",
        opportunity=opportunity,
    )


def evaluate(opportunities, rules, existing_keys=None):
    """Return alerts for rules that match, skipping previously-seen keys."""
    existing = set(existing_keys or ())
    seen = set()
    alerts = []
    for rule in rules:
        if not rule.enabled:
            continue
        for opportunity in opportunities:
            if not matches(rule, opportunity):
                continue
            key = alert_key(rule, opportunity)
            if key in existing or key in seen:
                continue
            seen.add(key)
            alerts.append(build_alert(rule, opportunity))
    return alerts


class Notifier(ABC):
    @abstractmethod
    def send(self, alert: Alert) -> None:
        """Deliver one alert."""


class InAppNotifier(Notifier):
    """Collect alerts and optionally persist them via a record callback."""

    def __init__(self, record=None):
        self._record = record
        self.sent = []

    def send(self, alert: Alert) -> None:
        self.sent.append(alert)
        if self._record is not None:
            self._record(alert)
