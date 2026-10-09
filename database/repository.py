"""SQLite persistence for bets and bankroll.

``commission`` is stored as a percentage (e.g. ``2.0``) to match the original
app. Schema changes go through :mod:`database.migrations`; data is never dropped.
"""

import json
from datetime import date, datetime, timedelta
from decimal import Decimal

from .migrations import DB_PATH, get_connection, migrate

__all__ = [
    "DB_PATH",
    "get_connection",
    "init_db",
    "insert_bet",
    "list_bets",
    "update_bet_status",
    "add_bankroll_entry",
    "list_bankroll",
    "create_offer",
    "list_offers",
    "get_offer",
    "update_offer_status",
    "update_offer",
    "link_offer_to_bet",
    "delete_offer",
    "create_alert_rule",
    "list_alert_rules",
    "set_alert_rule_enabled",
    "delete_alert_rule",
    "list_alert_history",
    "alert_dedup_keys",
    "record_alert",
    "record_opportunity",
    "record_odds_snapshot",
    "list_odds_snapshots",
    "count_odds_snapshots",
    "prune_odds_snapshots",
    "cap_odds_snapshots",
    "create_promotion",
    "list_promotions",
    "get_promotion",
    "update_promotion_status",
    "update_promotion",
    "set_promotion_eligibility",
    "add_promotion_source",
    "add_promotion_terms",
    "record_promotion_action",
    "add_promotion_reward",
    "link_promotion_match",
    "save_prediction_snapshot",
    "get_prediction_snapshot",
    "list_prediction_snapshots",
    "record_settlement",
    "list_settlements",
    "save_reconciliation",
    "list_reconciliations",
    "list_promotion_sources",
    "list_promotion_actions",
    "set_promotion_lifecycle",
    "list_lifecycle_history",
    "create_reward_token",
    "list_reward_tokens",
    "get_reward_token",
    "update_reward_token",
    "create_reservation",
    "set_reservation_status",
    "list_reservations",
    "update_reservation_amount",
    "list_reservations_for_bet",
    "reserved_totals",
]


def init_db(db_path=DB_PATH) -> None:
    migrate(db_path)


def insert_bet(
    event,
    bookmaker=None,
    exchange=None,
    market=None,
    selection=None,
    sport=None,
    bet_type="Qualifying bet",
    back_stake=0,
    back_odds=0,
    lay_odds=0,
    commission=0,
    lay_stake=0,
    liability=0,
    profit_if_back=0,
    profit_if_lay=0,
    lay_fee=None,
    source_type=None,
    is_live=None,
    provenance=None,
    status="Open",
    notes=None,
    source=None,
    event_date=None,
    db_path=DB_PATH,
) -> int:
    created_at = datetime.now().isoformat(timespec="seconds")
    if event_date is None:
        event_date = date.today()
    with get_connection(db_path) as connection:
        cursor = connection.execute(
            """INSERT INTO bets
            (created_at,event_date,bookmaker,exchange,event,market,selection,sport,bet_type,
             back_stake,back_odds,lay_odds,commission,lay_stake,liability,profit_if_back,
             profit_if_lay,lay_fee,source_type,is_live,provenance,status,notes,source)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                created_at,
                str(event_date),
                bookmaker,
                exchange,
                event,
                market,
                selection,
                sport,
                bet_type,
                back_stake,
                back_odds,
                lay_odds,
                commission,
                lay_stake,
                liability,
                profit_if_back,
                profit_if_lay,
                lay_fee,
                source_type,
                None if is_live is None else (1 if is_live else 0),
                provenance,
                status,
                notes,
                source,
            ),
        )
        return int(cursor.lastrowid)


def list_bets(db_path=DB_PATH) -> list:
    with get_connection(db_path) as connection:
        return connection.execute("SELECT * FROM bets ORDER BY id DESC").fetchall()


def update_bet_status(bet_id, status, db_path=DB_PATH) -> None:
    with get_connection(db_path) as connection:
        connection.execute("UPDATE bets SET status=? WHERE id=?", (status, int(bet_id)))


def add_bankroll_entry(account, amount, note=None, db_path=DB_PATH) -> int:
    created_at = datetime.now().isoformat(timespec="seconds")
    with get_connection(db_path) as connection:
        cursor = connection.execute(
            "INSERT INTO bankroll(created_at,account,amount,note) VALUES (?,?,?,?)",
            (created_at, account, amount, note),
        )
        return int(cursor.lastrowid)


def list_bankroll(db_path=DB_PATH) -> list:
    with get_connection(db_path) as connection:
        return connection.execute("SELECT * FROM bankroll ORDER BY id DESC").fetchall()


def create_offer(
    name,
    bookmaker=None,
    offer_type=None,
    qualifying_stake=0,
    min_odds=None,
    max_qualifying_loss=0,
    reward=0,
    reward_conversion=None,
    expiry=None,
    status="Available",
    notes=None,
    db_path=DB_PATH,
) -> int:
    created_at = datetime.now().isoformat(timespec="seconds")
    with get_connection(db_path) as connection:
        cursor = connection.execute(
            """INSERT INTO offers
            (created_at,updated_at,bookmaker,name,offer_type,qualifying_stake,min_odds,
             max_qualifying_loss,reward,reward_conversion,expiry,status,notes)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                created_at,
                created_at,
                bookmaker,
                name,
                offer_type,
                qualifying_stake,
                min_odds,
                max_qualifying_loss,
                reward,
                reward_conversion,
                str(expiry) if expiry is not None else None,
                status,
                notes,
            ),
        )
        return int(cursor.lastrowid)


def list_offers(db_path=DB_PATH) -> list:
    with get_connection(db_path) as connection:
        return connection.execute("SELECT * FROM offers ORDER BY id DESC").fetchall()


def get_offer(offer_id, db_path=DB_PATH):
    with get_connection(db_path) as connection:
        return connection.execute("SELECT * FROM offers WHERE id=?", (int(offer_id),)).fetchone()


def update_offer_status(offer_id, status, db_path=DB_PATH) -> None:
    updated_at = datetime.now().isoformat(timespec="seconds")
    with get_connection(db_path) as connection:
        connection.execute(
            "UPDATE offers SET status=?, updated_at=? WHERE id=?",
            (status, updated_at, int(offer_id)),
        )


def update_offer(offer_id, db_path=DB_PATH, **fields) -> None:
    allowed = {
        "bookmaker",
        "name",
        "offer_type",
        "qualifying_stake",
        "min_odds",
        "max_qualifying_loss",
        "reward",
        "reward_conversion",
        "expiry",
        "status",
        "notes",
        "bet_id",
    }
    updates = {key: value for key, value in fields.items() if key in allowed}
    if not updates:
        return
    updates["updated_at"] = datetime.now().isoformat(timespec="seconds")
    assignments = ", ".join(f"{key}=?" for key in updates)
    values = list(updates.values()) + [int(offer_id)]
    with get_connection(db_path) as connection:
        connection.execute(f"UPDATE offers SET {assignments} WHERE id=?", values)


def link_offer_to_bet(offer_id, bet_id, db_path=DB_PATH) -> None:
    update_offer(offer_id, db_path=db_path, bet_id=bet_id)


def delete_offer(offer_id, db_path=DB_PATH) -> None:
    with get_connection(db_path) as connection:
        connection.execute("DELETE FROM offers WHERE id=?", (int(offer_id),))


def create_alert_rule(
    name,
    enabled=True,
    kind=None,
    max_qualifying_loss_pct=None,
    max_back_lay_gap_pct=None,
    min_back_odds=None,
    max_required_liability=None,
    max_required_capital=None,
    bookmaker=None,
    sport=None,
    min_free_bet_conversion=None,
    notes=None,
    db_path=DB_PATH,
) -> int:
    created_at = datetime.now().isoformat(timespec="seconds")
    with get_connection(db_path) as connection:
        cursor = connection.execute(
            """INSERT INTO alert_rules
            (created_at,name,enabled,kind,max_qualifying_loss_pct,max_back_lay_gap_pct,
             min_back_odds,max_required_liability,max_required_capital,bookmaker,sport,
             min_free_bet_conversion,notes)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                created_at,
                name,
                1 if enabled else 0,
                kind,
                max_qualifying_loss_pct,
                max_back_lay_gap_pct,
                min_back_odds,
                max_required_liability,
                max_required_capital,
                bookmaker,
                sport,
                min_free_bet_conversion,
                notes,
            ),
        )
        return int(cursor.lastrowid)


def list_alert_rules(db_path=DB_PATH) -> list:
    with get_connection(db_path) as connection:
        return connection.execute("SELECT * FROM alert_rules ORDER BY id DESC").fetchall()


def set_alert_rule_enabled(rule_id, enabled, db_path=DB_PATH) -> None:
    with get_connection(db_path) as connection:
        connection.execute(
            "UPDATE alert_rules SET enabled=? WHERE id=?", (1 if enabled else 0, int(rule_id))
        )


def delete_alert_rule(rule_id, db_path=DB_PATH) -> None:
    with get_connection(db_path) as connection:
        connection.execute("DELETE FROM alert_rules WHERE id=?", (int(rule_id),))


def _float(value):
    return None if value is None else float(value)


def _bool_int(value):
    return None if value is None else (1 if value else 0)


def create_promotion(promotion, db_path=DB_PATH) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    with get_connection(db_path) as connection:
        cursor = connection.execute(
            """INSERT INTO promotions
            (created_at,updated_at,sportsbook,name,jurisdiction,offer_type,new_customer_only,
             qualifying_stake,qualifying_min_odds,qualifying_max_odds,reward_amount,reward_type,
             reward_count,stake_returned,reward_expiry_days,source_url,terms_text,status,
             confidence,eligibility,expires_at,lifecycle_status,official_source_url,
             terms_source_url,terms_verified_at,effective_date,withdrawal_restrictions,
             wagering_requirement_text,min_deposit,max_qualifying_stake)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                now, now,
                getattr(promotion, "sportsbook", None),
                getattr(promotion, "name", None),
                getattr(promotion, "jurisdiction", None),
                getattr(promotion, "offer_type", None),
                _bool_int(getattr(promotion, "new_customer_only", None)),
                _float(getattr(promotion, "qualifying_stake", None)),
                _float(getattr(promotion, "qualifying_min_odds", None)),
                _float(getattr(promotion, "qualifying_max_odds", None)),
                _float(getattr(promotion, "reward_amount", None)),
                getattr(promotion, "reward_type", None),
                getattr(promotion, "reward_count", None),
                _bool_int(getattr(promotion, "stake_returned", None)),
                getattr(promotion, "reward_expiry_days", None),
                getattr(promotion, "source_url", None),
                getattr(promotion, "terms_text", None),
                getattr(promotion, "status", "DISCOVERED"),
                getattr(promotion, "confidence", 0.0),
                getattr(promotion, "eligibility", "Unknown"),
                str(getattr(promotion, "expires_at", None)) if getattr(promotion, "expires_at", None) else None,
                getattr(promotion, "lifecycle_status", None) or getattr(promotion, "status", "DISCOVERED"),
                getattr(promotion, "official_source_url", None),
                getattr(promotion, "terms_source_url", None),
                str(getattr(promotion, "terms_verified_at", None)) if getattr(promotion, "terms_verified_at", None) else None,
                getattr(promotion, "effective_date", None),
                getattr(promotion, "withdrawal_restrictions", None),
                getattr(promotion, "wagering_requirement_text", None),
                _float(getattr(promotion, "min_deposit", None)),
                _float(getattr(promotion, "max_qualifying_stake", None)),
            ),
        )
        return int(cursor.lastrowid)


def list_promotions(db_path=DB_PATH) -> list:
    with get_connection(db_path) as connection:
        return connection.execute("SELECT * FROM promotions ORDER BY id DESC").fetchall()


def get_promotion(promotion_id, db_path=DB_PATH):
    with get_connection(db_path) as connection:
        return connection.execute(
            "SELECT * FROM promotions WHERE id=?", (int(promotion_id),)
        ).fetchone()


def update_promotion_status(promotion_id, status, db_path=DB_PATH) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    with get_connection(db_path) as connection:
        connection.execute(
            "UPDATE promotions SET status=?, updated_at=? WHERE id=?",
            (status, now, int(promotion_id)),
        )


def update_promotion(promotion_id, db_path=DB_PATH, **fields) -> None:
    allowed = {
        "sportsbook", "name", "jurisdiction", "offer_type", "new_customer_only",
        "qualifying_stake", "qualifying_min_odds", "qualifying_max_odds", "reward_amount",
        "reward_type", "reward_count", "stake_returned", "reward_expiry_days", "source_url",
        "terms_text", "official_source_url", "terms_source_url", "terms_verified_at",
        "effective_date", "withdrawal_restrictions", "wagering_requirement_text",
        "min_deposit", "max_qualifying_stake", "expires_at", "status", "lifecycle_status",
        "eligibility", "workflow_state",
    }
    updates = {}
    for key, value in fields.items():
        if key not in allowed:
            continue
        if isinstance(value, bool):
            value = 1 if value else 0
        elif isinstance(value, Decimal):
            value = float(value)
        elif isinstance(value, (datetime, date)):
            value = value.isoformat()
        updates[key] = value
    if not updates:
        return
    updates["updated_at"] = datetime.now().isoformat(timespec="seconds")
    assignments = ", ".join(f"{key}=?" for key in updates)
    values = list(updates.values()) + [int(promotion_id)]
    with get_connection(db_path) as connection:
        connection.execute(f"UPDATE promotions SET {assignments} WHERE id=?", values)


def set_promotion_eligibility(promotion_id, status, note=None, db_path=DB_PATH) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    with get_connection(db_path) as connection:
        connection.execute(
            "UPDATE promotions SET eligibility=?, updated_at=? WHERE id=?",
            (status, now, int(promotion_id)),
        )
        connection.execute(
            "INSERT INTO promotion_eligibility(promotion_id,created_at,status,note) VALUES (?,?,?,?)",
            (int(promotion_id), now, status, note),
        )


def add_promotion_source(
    promotion_id,
    url=None,
    text=None,
    note=None,
    source_type="THIRD_PARTY",
    jurisdiction=None,
    effective_date=None,
    expiry_date=None,
    version_hash=None,
    retrieved_at=None,
    db_path=DB_PATH,
) -> int:
    created = datetime.now().isoformat(timespec="seconds")
    retrieved = retrieved_at.isoformat() if retrieved_at else created
    with get_connection(db_path) as connection:
        cursor = connection.execute(
            """INSERT INTO promotion_sources
            (promotion_id,created_at,url,text,note,retrieved_at,source_type,jurisdiction,
             effective_date,expiry_date,version_hash)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                int(promotion_id), created, url, text, note, retrieved, source_type,
                jurisdiction, effective_date, expiry_date, version_hash,
            ),
        )
        return int(cursor.lastrowid)


def list_promotion_sources(promotion_id, db_path=DB_PATH) -> list:
    with get_connection(db_path) as connection:
        return connection.execute(
            "SELECT * FROM promotion_sources WHERE promotion_id=? ORDER BY id DESC",
            (int(promotion_id),),
        ).fetchall()


def set_promotion_lifecycle(promotion_id, new_status, note=None, db_path=DB_PATH) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    with get_connection(db_path) as connection:
        row = connection.execute(
            "SELECT status FROM promotions WHERE id=?", (int(promotion_id),)
        ).fetchone()
        previous = row["status"] if row else None
        connection.execute(
            "UPDATE promotions SET status=?, lifecycle_status=?, updated_at=? WHERE id=?",
            (new_status, new_status, now, int(promotion_id)),
        )
        connection.execute(
            """INSERT INTO promotion_lifecycle_history
            (promotion_id,created_at,previous_status,new_status,note) VALUES (?,?,?,?,?)""",
            (int(promotion_id), now, previous, new_status, note),
        )


def list_promotion_actions(promotion_id, db_path=DB_PATH) -> list:
    with get_connection(db_path) as connection:
        return connection.execute(
            "SELECT * FROM promotion_actions WHERE promotion_id=? ORDER BY id ASC",
            (int(promotion_id),),
        ).fetchall()


def list_lifecycle_history(promotion_id, db_path=DB_PATH) -> list:
    with get_connection(db_path) as connection:
        return connection.execute(
            "SELECT * FROM promotion_lifecycle_history WHERE promotion_id=? ORDER BY id ASC",
            (int(promotion_id),),
        ).fetchall()


def create_reward_token(token, db_path=DB_PATH) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    key = getattr(token, "token_key", None) or f"{token.promotion_id}:{token.face_value}:{now}"
    with get_connection(db_path) as connection:
        cursor = connection.execute(
            """INSERT OR IGNORE INTO promotion_reward_tokens
            (created_at,promotion_id,token_key,face_value,reward_type,eligible_markets,
             received_at,expires_at,used_at,status,linked_qualifying_bet_id,
             linked_conversion_bet_id,realized_value,note)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                now, int(token.promotion_id), key, _float(token.face_value), token.reward_type,
                json.dumps(list(token.eligible_markets or ())),
                token.received_at.isoformat() if token.received_at else None,
                token.expires_at.isoformat() if token.expires_at else None,
                token.used_at.isoformat() if token.used_at else None,
                token.status, token.linked_qualifying_bet_id, token.linked_conversion_bet_id,
                _float(token.realized_value), getattr(token, "note", None),
            ),
        )
        if cursor.rowcount == 1:
            return int(cursor.lastrowid)
        row = connection.execute(
            "SELECT id FROM promotion_reward_tokens WHERE token_key=?", (key,)
        ).fetchone()
        return int(row["id"])


def list_reward_tokens(promotion_id=None, db_path=DB_PATH) -> list:
    with get_connection(db_path) as connection:
        if promotion_id is None:
            return connection.execute(
                "SELECT * FROM promotion_reward_tokens ORDER BY id DESC"
            ).fetchall()
        return connection.execute(
            "SELECT * FROM promotion_reward_tokens WHERE promotion_id=? ORDER BY id DESC",
            (int(promotion_id),),
        ).fetchall()


def get_reward_token(token_id, db_path=DB_PATH):
    with get_connection(db_path) as connection:
        return connection.execute(
            "SELECT * FROM promotion_reward_tokens WHERE id=?", (int(token_id),)
        ).fetchone()


def update_reward_token(token_id, db_path=DB_PATH, **fields) -> None:
    allowed = {
        "face_value", "reward_type", "eligible_markets", "received_at", "expires_at",
        "used_at", "status", "linked_qualifying_bet_id", "linked_conversion_bet_id",
        "realized_value", "note",
    }
    updates = {key: value for key, value in fields.items() if key in allowed}
    if not updates:
        return
    if "eligible_markets" in updates and not isinstance(updates["eligible_markets"], str):
        updates["eligible_markets"] = json.dumps(list(updates["eligible_markets"]))
    if "realized_value" in updates:
        updates["realized_value"] = _float(updates["realized_value"])
    assignments = ", ".join(f"{key}=?" for key in updates)
    values = list(updates.values()) + [int(token_id)]
    with get_connection(db_path) as connection:
        connection.execute(
            f"UPDATE promotion_reward_tokens SET {assignments} WHERE id=?", values
        )


def add_promotion_terms(promotion_id, source=None, structured=None, db_path=DB_PATH) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    with get_connection(db_path) as connection:
        cursor = connection.execute(
            "INSERT INTO promotion_terms(promotion_id,created_at,source,structured) VALUES (?,?,?,?)",
            (int(promotion_id), now, source, structured),
        )
        return int(cursor.lastrowid)


def record_promotion_action(promotion_id, action, bet_id=None, note=None, db_path=DB_PATH) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    with get_connection(db_path) as connection:
        cursor = connection.execute(
            "INSERT INTO promotion_actions(promotion_id,created_at,action,bet_id,note) VALUES (?,?,?,?,?)",
            (int(promotion_id), now, action, bet_id, note),
        )
        return int(cursor.lastrowid)


def add_promotion_reward(promotion_id, amount, reward_type=None, status=None, note=None, db_path=DB_PATH) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    with get_connection(db_path) as connection:
        cursor = connection.execute(
            "INSERT INTO promotion_rewards(promotion_id,created_at,amount,reward_type,status,note) VALUES (?,?,?,?,?,?)",
            (int(promotion_id), now, _float(amount), reward_type, status, note),
        )
        return int(cursor.lastrowid)


def link_promotion_match(promotion_id, event_id=None, selection=None, opportunity_ref=None, db_path=DB_PATH) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    with get_connection(db_path) as connection:
        cursor = connection.execute(
            "INSERT INTO promotion_matches(promotion_id,created_at,event_id,selection,opportunity_ref,note) VALUES (?,?,?,?,?,?)",
            (int(promotion_id), now, event_id, selection, opportunity_ref, None),
        )
        return int(cursor.lastrowid)


def record_opportunity(opportunity, db_path=DB_PATH) -> int:
    created_at = datetime.now().isoformat(timespec="seconds")
    provenance = getattr(opportunity, "provenance", None)
    if provenance is not None and hasattr(provenance, "as_dict"):
        provenance_json = json.dumps(provenance.as_dict())
        snapshot_id = getattr(provenance, "liquidity_snapshot_id", None)
    else:
        provenance_json = str(provenance) if provenance is not None else None
        snapshot_id = None
    with get_connection(db_path) as connection:
        cursor = connection.execute(
            """INSERT INTO opportunities
            (created_at,event_id,selection,market,kind,book_provider,exchange_provider,
             back_odds,lay_odds,qualifying_loss,qualifying_loss_pct,required_capital,
             required_liability,rating,event_confidence,market_confidence,quote_timestamp,
             source_type,is_live,liquidity_snapshot_id,provenance)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                created_at,
                opportunity.event_id,
                opportunity.selection,
                opportunity.market,
                getattr(opportunity.kind, "value", str(opportunity.kind)),
                opportunity.book_provider,
                opportunity.exchange_provider,
                float(opportunity.back_odds),
                float(opportunity.lay_odds),
                float(opportunity.qualifying_loss),
                float(opportunity.qualifying_loss_pct),
                float(opportunity.required_capital),
                float(opportunity.lay_liability),
                float(opportunity.rating_percent),
                opportunity.event_confidence,
                opportunity.market_confidence,
                opportunity.timestamp.isoformat() if opportunity.timestamp else None,
                getattr(opportunity, "source_type", "MOCK"),
                None if getattr(opportunity, "is_live", None) is None else (1 if opportunity.is_live else 0),
                snapshot_id,
                provenance_json,
            ),
        )
        return int(cursor.lastrowid)


def _fills_json(fills):
    return json.dumps([[str(price), str(contracts)] for price, contracts in fills or []])


def save_prediction_snapshot(prediction, db_path=DB_PATH) -> int:
    """Persist an immutable prediction snapshot (first write for a bet wins)."""
    now = datetime.now().isoformat(timespec="seconds")
    provenance = prediction.source_provenance
    if provenance is not None and hasattr(provenance, "as_dict"):
        provenance_json = json.dumps(provenance.as_dict())
    else:
        provenance_json = str(provenance) if provenance is not None else None
    payload = json.dumps(
        {
            "predicted_fills": json.loads(_fills_json(prediction.predicted_fills)),
            "timestamp": prediction.timestamp.isoformat() if prediction.timestamp else None,
        }
    )
    with get_connection(db_path) as connection:
        cursor = connection.execute(
            """INSERT OR IGNORE INTO prediction_snapshots
            (created_at,bet_id,sportsbook,exchange,event,market,selection,
             predicted_back_stake,predicted_back_odds,predicted_lay_contracts,
             predicted_lay_odds,predicted_liability,predicted_exchange_fee,
             predicted_back_win_pnl,predicted_lay_win_pnl,predicted_total_capital,
             predicted_qualifying_loss,max_price,fee_factor,calculation_version,
             fee_model_version,source_provenance,payload_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                now, int(prediction.bet_id), prediction.sportsbook, prediction.exchange,
                prediction.event, prediction.market, prediction.selection,
                _float(prediction.predicted_back_stake), _float(prediction.predicted_back_odds),
                _float(prediction.predicted_lay_contracts), _float(prediction.predicted_lay_odds),
                _float(prediction.predicted_liability), _float(prediction.predicted_exchange_fee),
                _float(prediction.predicted_back_win_pnl), _float(prediction.predicted_lay_win_pnl),
                _float(prediction.predicted_total_capital), _float(prediction.predicted_qualifying_loss),
                _float(prediction.max_price), _float(prediction.fee_factor),
                prediction.calculation_version, prediction.fee_model_version,
                provenance_json, payload,
            ),
        )
        if cursor.rowcount == 1:
            return int(cursor.lastrowid)
        row = connection.execute(
            "SELECT id FROM prediction_snapshots WHERE bet_id=?", (int(prediction.bet_id),)
        ).fetchone()
        return int(row["id"])


def get_prediction_snapshot(bet_id, db_path=DB_PATH):
    with get_connection(db_path) as connection:
        return connection.execute(
            "SELECT * FROM prediction_snapshots WHERE bet_id=?", (int(bet_id),)
        ).fetchone()


def list_prediction_snapshots(db_path=DB_PATH) -> list:
    with get_connection(db_path) as connection:
        return connection.execute("SELECT * FROM prediction_snapshots ORDER BY id DESC").fetchall()


def record_settlement(actual, db_path=DB_PATH) -> bool:
    now = datetime.now().isoformat(timespec="seconds")
    settled_at = actual.settled_at.isoformat() if actual.settled_at else now
    key = actual.settlement_key or f"{actual.bet_id}:{settled_at}:{actual.outcome}"
    payload = json.dumps({"actual_fill_prices": json.loads(_fills_json(actual.actual_fill_prices))})
    with get_connection(db_path) as connection:
        cursor = connection.execute(
            """INSERT OR IGNORE INTO settlements
            (created_at,bet_id,settlement_key,source,outcome,actual_back_stake,
             actual_back_odds,actual_lay_contracts,actual_fill_prices,actual_liability,
             actual_exchange_fee,actual_sportsbook_settlement,actual_exchange_settlement,
             actual_total_pnl,settled_at,payload_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                now, int(actual.bet_id), key, actual.source, actual.outcome,
                _float(actual.actual_back_stake), _float(actual.actual_back_odds),
                _float(actual.actual_lay_contracts), _fills_json(actual.actual_fill_prices),
                _float(actual.actual_liability), _float(actual.actual_exchange_fee),
                _float(actual.actual_sportsbook_settlement), _float(actual.actual_exchange_settlement),
                _float(actual.actual_total_pnl), settled_at, payload,
            ),
        )
        return cursor.rowcount == 1


def list_settlements(bet_id=None, db_path=DB_PATH) -> list:
    with get_connection(db_path) as connection:
        if bet_id is None:
            return connection.execute("SELECT * FROM settlements ORDER BY id DESC").fetchall()
        return connection.execute(
            "SELECT * FROM settlements WHERE bet_id=? ORDER BY id DESC", (int(bet_id),)
        ).fetchall()


def save_reconciliation(result, prediction_id=None, settlement_id=None, db_path=DB_PATH) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    payload = json.dumps(
        {"differences": [d.metric for d in result.differences]}
    )
    with get_connection(db_path) as connection:
        cursor = connection.execute(
            """INSERT OR IGNORE INTO reconciliations
            (created_at,bet_id,prediction_id,settlement_id,status,predicted_pnl,
             actual_pnl,pnl_difference,fee_difference,liability_difference,
             contracts_difference,flags,message,payload_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                now, int(result.bet_id), prediction_id, settlement_id, result.status,
                _float(result.predicted_pnl), _float(result.actual_pnl),
                _float(result.pnl_difference), _float(result.fee_difference),
                _float(result.liability_difference), _float(result.contracts_difference),
                "; ".join(result.flags), result.message, payload,
            ),
        )
        return cursor.rowcount


def list_reconciliations(db_path=DB_PATH) -> list:
    with get_connection(db_path) as connection:
        return connection.execute("SELECT * FROM reconciliations ORDER BY id DESC").fetchall()


def create_reservation(
    account, kind, amount, bet_id=None, promotion_id=None, note=None, db_path=DB_PATH
) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    with get_connection(db_path) as connection:
        cursor = connection.execute(
            """INSERT INTO bankroll_reservations
            (created_at,updated_at,promotion_id,bet_id,account,kind,amount,status,note)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (now, now, promotion_id, bet_id, account, kind, _float(amount), "RESERVED", note),
        )
        return int(cursor.lastrowid)


def set_reservation_status(reservation_id, status, db_path=DB_PATH) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    with get_connection(db_path) as connection:
        connection.execute(
            "UPDATE bankroll_reservations SET status=?, updated_at=? WHERE id=?",
            (status, now, int(reservation_id)),
        )


def update_reservation_amount(reservation_id, amount, db_path=DB_PATH) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    with get_connection(db_path) as connection:
        connection.execute(
            "UPDATE bankroll_reservations SET amount=?, updated_at=? WHERE id=?",
            (_float(amount), now, int(reservation_id)),
        )


def list_reservations_for_bet(bet_id, db_path=DB_PATH) -> list:
    with get_connection(db_path) as connection:
        return connection.execute(
            "SELECT * FROM bankroll_reservations WHERE bet_id=? ORDER BY id ASC",
            (int(bet_id),),
        ).fetchall()


def list_reservations(status=None, db_path=DB_PATH) -> list:
    with get_connection(db_path) as connection:
        if status is None:
            return connection.execute(
                "SELECT * FROM bankroll_reservations ORDER BY id DESC"
            ).fetchall()
        return connection.execute(
            "SELECT * FROM bankroll_reservations WHERE status=? ORDER BY id DESC", (status,)
        ).fetchall()


def reserved_totals(db_path=DB_PATH) -> dict:
    totals = {}
    with get_connection(db_path) as connection:
        rows = connection.execute(
            "SELECT account, SUM(amount) AS amount FROM bankroll_reservations "
            "WHERE status='RESERVED' GROUP BY account"
        ).fetchall()
    for row in rows:
        totals[row["account"]] = Decimal(str(row["amount"] or 0))
    return totals


def record_odds_snapshot(
    provider,
    event_id,
    market,
    selection,
    side,
    decimal_odds,
    available_size=None,
    commission=None,
    quote_timestamp=None,
    db_path=DB_PATH,
) -> int:
    recorded_at = datetime.now().isoformat(timespec="seconds")
    with get_connection(db_path) as connection:
        cursor = connection.execute(
            """INSERT INTO odds_snapshots
            (recorded_at,provider,event_id,market,selection,side,decimal_odds,
             available_size,commission,quote_timestamp)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                recorded_at,
                provider,
                event_id,
                market,
                selection,
                side,
                decimal_odds,
                available_size,
                commission,
                quote_timestamp,
            ),
        )
        return int(cursor.lastrowid)


def list_odds_snapshots(db_path=DB_PATH) -> list:
    with get_connection(db_path) as connection:
        return connection.execute("SELECT * FROM odds_snapshots ORDER BY id DESC").fetchall()


def count_odds_snapshots(db_path=DB_PATH) -> int:
    with get_connection(db_path) as connection:
        return int(connection.execute("SELECT COUNT(*) FROM odds_snapshots").fetchone()[0])


def prune_odds_snapshots(retention_days=7, db_path=DB_PATH) -> int:
    cutoff = (datetime.now() - timedelta(days=retention_days)).isoformat(timespec="seconds")
    with get_connection(db_path) as connection:
        cursor = connection.execute(
            "DELETE FROM odds_snapshots WHERE recorded_at < ?", (cutoff,)
        )
        return cursor.rowcount


def cap_odds_snapshots(max_rows=100000, db_path=DB_PATH) -> int:
    with get_connection(db_path) as connection:
        cursor = connection.execute(
            """DELETE FROM odds_snapshots WHERE id NOT IN (
                SELECT id FROM odds_snapshots ORDER BY id DESC LIMIT ?
            )""",
            (int(max_rows),),
        )
        return cursor.rowcount


def list_alert_history(db_path=DB_PATH) -> list:
    with get_connection(db_path) as connection:
        return connection.execute("SELECT * FROM alert_history ORDER BY id DESC").fetchall()


def alert_dedup_keys(db_path=DB_PATH) -> set:
    with get_connection(db_path) as connection:
        return {row[0] for row in connection.execute("SELECT dedup_key FROM alert_history")}


def record_alert(
    rule_id,
    rule_name,
    dedup_key,
    event_id=None,
    selection=None,
    bookmaker=None,
    exchange=None,
    message=None,
    db_path=DB_PATH,
) -> bool:
    created_at = datetime.now().isoformat(timespec="seconds")
    with get_connection(db_path) as connection:
        cursor = connection.execute(
            """INSERT OR IGNORE INTO alert_history
            (created_at,rule_id,rule_name,dedup_key,event_id,selection,bookmaker,exchange,message)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                created_at,
                rule_id,
                rule_name,
                dedup_key,
                event_id,
                selection,
                bookmaker,
                exchange,
                message,
            ),
        )
        return cursor.rowcount == 1
