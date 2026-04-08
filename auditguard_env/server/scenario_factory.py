"""Deterministic scenario generation for AuditGuard."""

from __future__ import annotations

from datetime import datetime, timedelta
from random import Random

from server.models import CompanyPolicy, ExpenseItemView, ScenarioInstance


_VALID_SCENARIOS = {"easy", "medium", "hard"}


def _policy_for_scenario(name: str) -> CompanyPolicy:
    base_caps = {
        "travel": 900.0,
        "lodging": 1200.0,
        "meals": 120.0,
        "ground_transport": 150.0,
        "office_supplies": 300.0,
        "software": 500.0,
        "training": 700.0,
    }
    if name == "easy":
        receipt_threshold = 75.0
        blocked = ["7995"]
        forbidden = ["CASH DEPOT", "GIFT HUB"]
    elif name == "medium":
        receipt_threshold = 60.0
        blocked = ["7995", "6051"]
        forbidden = ["CASH DEPOT", "GIFT HUB", "WIRE QUICK"]
    else:
        receipt_threshold = 50.0
        blocked = ["7995", "6051", "4829"]
        forbidden = ["CASH DEPOT", "GIFT HUB", "WIRE QUICK", "CRYPTO KIOSK"]

    return CompanyPolicy(
        receipt_required_over_amount=receipt_threshold,
        caps_by_category=base_caps,
        forbidden_merchants=forbidden,
        blocked_mccs=blocked,
        required_fields=[
            "submitted_category",
            "submitted_amount",
            "date_time",
            "merchant_descriptor",
            "merchant_mcc",
        ],
        split_transaction_window_hours=4,
        split_transaction_total_threshold_by_category={
            "meals": 120.0,
            "ground_transport": 150.0,
            "office_supplies": 300.0,
        },
    )


def _merchant_catalog() -> list[tuple[str, str, str]]:
    return [
        ("SKYJET AIR", "4511", "travel"),
        ("URBAN STAY HOTEL", "7011", "lodging"),
        ("CITY CAB", "4121", "ground_transport"),
        ("LAPTOP CENTRAL", "5732", "office_supplies"),
        ("TEAM LUNCH CAFE", "5812", "meals"),
        ("SAASWORKS", "5734", "software"),
        ("LEARNHUB", "8299", "training"),
        ("CASH DEPOT", "6051", "office_supplies"),
        ("GIFT HUB", "5947", "office_supplies"),
        ("WIRE QUICK", "4829", "travel"),
        ("CRYPTO KIOSK", "6051", "travel"),
    ]


def _vague_note(rng: Random) -> str:
    return rng.choice(
        [
            "business",
            "misc",
            "team sync",
            "client thing",
            "urgent",
            "n/a",
            "meeting",
        ]
    )


def _specific_note(rng: Random, category: str) -> str:
    notes = {
        "travel": ["Flight to client site", "Return flight for audit visit"],
        "lodging": ["Hotel during conference", "Overnight stay for onsite work"],
        "ground_transport": ["Airport transfer", "Client office taxi"],
        "office_supplies": ["Printer paper restock", "Cables and adapters"],
        "meals": ["Working lunch with client", "Team dinner after workshop"],
        "software": ["Project management license", "Security tool subscription"],
        "training": ["Compliance training seat", "Data policy webinar pass"],
    }
    return rng.choice(notes.get(category, ["business expense"]))


def _round2(val: float) -> float:
    return float(f"{val:.2f}")


def _base_item(
    rng: Random,
    idx: int,
    policy: CompanyPolicy,
    base_time: datetime,
    merchant: tuple[str, str, str] | None = None,
) -> ExpenseItemView:
    if merchant is None:
        merchant = rng.choice(_merchant_catalog())
    merchant_name, merchant_mcc, default_category = merchant
    category = default_category
    cap = policy.caps_by_category.get(category, 200.0)

    low = max(8.0, cap * 0.2)
    high = max(low + 5.0, cap * 0.82)
    amount = _round2(rng.uniform(low, high))
    dt = base_time + timedelta(minutes=idx * rng.randint(23, 89))
    receipt_present = rng.random() > 0.15
    receipt_total = _round2(amount + rng.uniform(-1.25, 1.25)) if receipt_present else None
    receipt_id = f"R-{rng.randint(100000, 999999)}" if receipt_present else None
    receipt_hash = (
        f"H{rng.randint(10**9, 10**10 - 1)}{idx}" if receipt_present else None
    )

    return ExpenseItemView(
        item_id=f"EXP-{idx:03d}",
        submitted_category=category,
        submitted_amount=amount,
        currency="USD",
        date_time=dt.replace(microsecond=0).isoformat(),
        merchant_descriptor=merchant_name,
        receipt_present=receipt_present,
        receipt_id=receipt_id,
        receipt_total=receipt_total,
        receipt_hash=receipt_hash,
        merchant_mcc=merchant_mcc,
        employee_note=_specific_note(rng, category),
    )


def _inject_easy_patterns(
    rng: Random,
    items: list[ExpenseItemView],
    policy: CompanyPolicy,
    hidden: dict[str, dict[str, str]],
) -> None:
    forbidden_target = items[rng.randrange(len(items))]
    forbidden_target.merchant_descriptor = "CASH DEPOT"
    forbidden_target.merchant_mcc = "6051"
    forbidden_target.submitted_category = "office_supplies"
    forbidden_target.submitted_amount = _round2(policy.caps_by_category["office_supplies"] + 95.0)
    forbidden_target.receipt_present = False
    forbidden_target.receipt_id = None
    forbidden_target.receipt_total = None
    forbidden_target.receipt_hash = None
    forbidden_target.employee_note = _vague_note(rng)
    hidden[forbidden_target.item_id] = {"signal": "obvious_forbidden_merchant"}

    second = items[(items.index(forbidden_target) + 1) % len(items)]
    second.merchant_descriptor = "GIFT HUB"
    second.merchant_mcc = "5947"
    second.submitted_category = "office_supplies"
    second.submitted_amount = _round2(policy.caps_by_category["office_supplies"] + 35.0)
    second.receipt_present = True
    second.receipt_total = second.submitted_amount
    second.employee_note = "gift cards"
    hidden[second.item_id] = {"signal": "obvious_forbidden_merchant_secondary"}


def _inject_medium_patterns(
    rng: Random,
    items: list[ExpenseItemView],
    policy: CompanyPolicy,
    hidden: dict[str, dict[str, str]],
) -> None:
    # Missing receipt above threshold.
    missing_target = items[rng.randrange(len(items))]
    missing_target.submitted_amount = _round2(policy.receipt_required_over_amount + 28.0)
    missing_target.receipt_present = False
    missing_target.receipt_id = None
    missing_target.receipt_total = None
    missing_target.receipt_hash = None
    missing_target.employee_note = _vague_note(rng)
    hidden[missing_target.item_id] = {"signal": "missing_receipt_over_threshold"}

    # Split transaction pair in meals within policy window.
    meals_threshold = policy.split_transaction_total_threshold_by_category["meals"]
    i1 = 0
    i2 = 1 if len(items) > 1 else 0
    item1 = items[i1]
    item2 = items[i2]
    base_t = datetime.fromisoformat(item1.date_time)

    item1.submitted_category = "meals"
    item1.merchant_descriptor = "TEAM LUNCH CAFE"
    item1.merchant_mcc = "5812"
    item1.submitted_amount = _round2((meals_threshold * 0.48) + rng.uniform(0.5, 3.0))
    item1.date_time = base_t.replace(microsecond=0).isoformat()
    item1.employee_note = _vague_note(rng)

    item2.submitted_category = "meals"
    item2.merchant_descriptor = "TEAM LUNCH CAFE"
    item2.merchant_mcc = "5812"
    item2.submitted_amount = _round2((meals_threshold * 0.58) + rng.uniform(0.5, 3.0))
    item2.date_time = (base_t + timedelta(hours=1, minutes=20)).replace(microsecond=0).isoformat()
    item2.employee_note = _vague_note(rng)
    hidden[item1.item_id] = {"signal": "split_transaction_pair"}
    hidden[item2.item_id] = {"signal": "split_transaction_pair"}


def _inject_hard_patterns(
    rng: Random,
    items: list[ExpenseItemView],
    policy: CompanyPolicy,
    hidden: dict[str, dict[str, str]],
) -> None:
    meals_threshold = policy.split_transaction_total_threshold_by_category["meals"]
    anchor = rng.randrange(len(items) - 2)
    split_set = [items[anchor], items[anchor + 1], items[anchor + 2]]

    running_total = 0.0
    for i, it in enumerate(split_set):
        it.submitted_category = "meals"
        amount = _round2((meals_threshold / 3.0) + rng.uniform(-2.0, 2.0))
        running_total = _round2(running_total + amount)
        it.submitted_amount = amount
        it.merchant_descriptor = "TEAM LUNCH CAFE"
        it.merchant_mcc = "5812"
        it.employee_note = _vague_note(rng)
        if i > 0:
            prev_time = datetime.fromisoformat(split_set[i - 1].date_time)
            it.date_time = (prev_time + timedelta(minutes=rng.randint(38, 74))).isoformat()
        hidden[it.item_id] = {"signal": "potential_split_transaction"}

    if running_total <= meals_threshold:
        split_set[-1].submitted_amount = _round2(split_set[-1].submitted_amount + 9.5)

    # Subtle signal: merchant/category mismatch with near-threshold amount.
    subtle = items[(anchor + 4) % len(items)]
    subtle.merchant_descriptor = "SKYJET AIR"
    subtle.merchant_mcc = "4511"
    subtle.submitted_category = "office_supplies"
    subtle.submitted_amount = _round2(policy.caps_by_category["office_supplies"] - 2.5)
    subtle.employee_note = _vague_note(rng)
    hidden[subtle.item_id] = {"signal": "subtle_category_mcc_mismatch"}


def _ensure_split_transaction_case(
    rng: Random,
    items: list[ExpenseItemView],
    policy: CompanyPolicy,
    hidden: dict[str, dict[str, str]],
) -> None:
    if len(items) < 2:
        return

    category = "office_supplies"
    threshold = policy.split_transaction_total_threshold_by_category.get(category)
    if threshold is None:
        return

    item1 = items[0]
    item2 = items[1]

    t0 = datetime.fromisoformat(item1.date_time)
    window = max(1, min(policy.split_transaction_window_hours, 4))

    amt1 = _round2((threshold * 0.46) + rng.uniform(-2.0, 2.0))
    amt2 = _round2((threshold * 0.58) + rng.uniform(-2.0, 2.0))
    if amt1 + amt2 <= threshold:
        amt2 = _round2(threshold - amt1 + 12.0)

    for target, dt, amount in (
        (item1, t0, amt1),
        (item2, t0 + timedelta(hours=max(1, window - 1)), amt2),
    ):
        target.submitted_category = category
        target.merchant_descriptor = "GIFT HUB"
        target.merchant_mcc = "5947"
        target.submitted_amount = amount
        target.date_time = dt.replace(microsecond=0).isoformat()
        target.employee_note = _vague_note(rng)

    hidden[item1.item_id] = {"signal": "guaranteed_split_transaction_pair"}
    hidden[item2.item_id] = {"signal": "guaranteed_split_transaction_pair"}


def make_scenario(name: str, seed: int) -> ScenarioInstance:
    """Create a deterministic scenario instance for the given scenario name and seed."""
    scenario = name.lower().strip()
    if scenario not in _VALID_SCENARIOS:
        raise ValueError("Scenario must be one of: easy, medium, hard")

    rng = Random(seed)
    policy = _policy_for_scenario(scenario)
    base_time = datetime(2025, 2, 3, 9, 0, 0) + timedelta(days=seed % 17)

    if scenario == "easy":
        count = rng.randint(6, 8)
        budget_total = 8
        brief = "Review obvious fraud in a small batch, with clear forbidden-merchant violations."
    elif scenario == "medium":
        count = rng.randint(8, 10)
        budget_total = 12
        brief = "Review a medium batch with mixed signals (missing receipt and split transactions)."
    else:
        count = rng.randint(10, 14)
        budget_total = 16
        brief = "Review a hard batch with subtle, low-visibility fraud patterns."

    items = [_base_item(rng, idx + 1, policy, base_time) for idx in range(count)]
    hidden_truth: dict[str, dict[str, str]] = {}

    if scenario == "easy":
        _inject_easy_patterns(rng, items, policy, hidden_truth)
    elif scenario == "medium":
        _inject_medium_patterns(rng, items, policy, hidden_truth)
    else:
        _inject_hard_patterns(rng, items, policy, hidden_truth)

    _ensure_split_transaction_case(rng, items, policy, hidden_truth)

    return ScenarioInstance(
        scenario=scenario,
        seed=seed,
        task_brief=brief,
        company_policy=policy,
        items=items,
        audit_budget_total=budget_total,
        allowed_reason_codes=[
            "MISSING_RECEIPT",
            "OVER_CAP",
            "DUPLICATE",
            "MCC_MISMATCH",
            "SPLIT_TRANSACTION",
            "FORBIDDEN_MERCHANT",
            "BLOCKED_MCC",
            "RECEIPT_MISMATCH",
            "NEEDS_INFO",
            "APPROVE_OK",
        ],
        hidden={"truth_by_item": hidden_truth},
    )
