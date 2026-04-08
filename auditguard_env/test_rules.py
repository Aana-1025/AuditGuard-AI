from __future__ import annotations

from server.models import CompanyPolicy, ExpenseItemView
from server.rules_engine import evaluate_rules


def make_policy() -> CompanyPolicy:
    return CompanyPolicy(
        receipt_required_over_amount=75.0,
        caps_by_category={
            "meals": 120.0,
            "office_supplies": 300.0,
        },
        forbidden_merchants=["CASH DEPOT"],
        blocked_mccs=[],
        required_fields=[],
        split_transaction_window_hours=4,
        split_transaction_total_threshold_by_category={
            "meals": 120.0,
            "office_supplies": 300.0,
        },
    )


def make_item(
    item_id: str,
    *,
    category: str,
    amount: float,
    merchant: str,
    date_time: str,
    receipt_present: bool = True,
    receipt_hash: str | None = None,
) -> ExpenseItemView:
    return ExpenseItemView(
        item_id=item_id,
        submitted_category=category,
        submitted_amount=amount,
        currency="USD",
        date_time=date_time,
        merchant_descriptor=merchant,
        receipt_present=receipt_present,
        receipt_id=f"R-{item_id}" if receipt_present else None,
        receipt_total=amount if receipt_present else None,
        receipt_hash=receipt_hash,
        merchant_mcc="0000",
        employee_note="test",
    )


def main() -> None:
    policy = make_policy()
    items = [
        make_item(
            "EXP-001",
            category="office_supplies",
            amount=350.0,
            merchant="CASH DEPOT",
            date_time="2025-02-03T09:00:00",
            receipt_present=False,
        ),
        make_item(
            "EXP-002",
            category="office_supplies",
            amount=40.0,
            merchant="OFFICE MART",
            date_time="2025-02-03T10:00:00",
            receipt_hash="dup-1",
        ),
        make_item(
            "EXP-003",
            category="office_supplies",
            amount=42.0,
            merchant="OFFICE MART",
            date_time="2025-02-03T10:05:00",
            receipt_hash="dup-1",
        ),
        make_item(
            "EXP-004",
            category="meals",
            amount=70.0,
            merchant="TEAM LUNCH CAFE",
            date_time="2025-02-03T11:00:00",
        ),
        make_item(
            "EXP-005",
            category="meals",
            amount=60.0,
            merchant="TEAM LUNCH CAFE",
            date_time="2025-02-03T12:00:00",
        ),
        make_item(
            "EXP-006",
            category="meals",
            amount=30.0,
            merchant="TEAM LUNCH CAFE",
            date_time="2025-02-03T20:00:00",
        ),
    ]

    findings = evaluate_rules(items, policy)

    assert findings["EXP-001"] == [
        "FORBIDDEN_MERCHANT",
        "MISSING_RECEIPT",
        "OVER_CAP",
    ]
    assert findings["EXP-002"] == ["DUPLICATE"]
    assert findings["EXP-003"] == ["DUPLICATE"]
    assert findings["EXP-004"] == ["SPLIT_TRANSACTION"]
    assert findings["EXP-005"] == ["SPLIT_TRANSACTION"]
    assert findings["EXP-006"] == []

    print("rules_engine tests passed")


if __name__ == "__main__":
    main()
