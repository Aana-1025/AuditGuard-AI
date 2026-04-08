from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from server.models import CompanyPolicy, ExpenseItemView


def evaluate_rules(
    items: list[ExpenseItemView],
    policy: CompanyPolicy,
) -> dict[str, list[str]]:
    findings: dict[str, list[str]] = {item.item_id: [] for item in items}

    _apply_forbidden_merchant(items, policy, findings)
    _apply_missing_receipt(items, policy, findings)
    _apply_over_cap(items, policy, findings)
    _apply_duplicate_receipt_hash(items, findings)
    _apply_split_transactions(items, policy, findings)

    return findings


def _add_reason(findings: dict[str, list[str]], item_id: str, reason_code: str) -> None:
    reasons = findings.setdefault(item_id, [])
    if reason_code not in reasons:
        reasons.append(reason_code)


def _apply_forbidden_merchant(
    items: list[ExpenseItemView],
    policy: CompanyPolicy,
    findings: dict[str, list[str]],
) -> None:
    forbidden_merchants = set(policy.forbidden_merchants)
    for item in items:
        if item.merchant_descriptor in forbidden_merchants:
            _add_reason(findings, item.item_id, "FORBIDDEN_MERCHANT")


def _apply_missing_receipt(
    items: list[ExpenseItemView],
    policy: CompanyPolicy,
    findings: dict[str, list[str]],
) -> None:
    threshold = policy.receipt_required_over_amount
    for item in items:
        if item.submitted_amount > threshold and not item.receipt_present:
            _add_reason(findings, item.item_id, "MISSING_RECEIPT")


def _apply_over_cap(
    items: list[ExpenseItemView],
    policy: CompanyPolicy,
    findings: dict[str, list[str]],
) -> None:
    for item in items:
        cap = policy.caps_by_category.get(item.submitted_category)
        if cap is not None and item.submitted_amount > cap:
            _add_reason(findings, item.item_id, "OVER_CAP")


def _apply_duplicate_receipt_hash(
    items: list[ExpenseItemView],
    findings: dict[str, list[str]],
) -> None:
    items_by_hash: dict[str, list[ExpenseItemView]] = defaultdict(list)
    for item in items:
        if item.receipt_hash:
            items_by_hash[item.receipt_hash].append(item)

    for matching_items in items_by_hash.values():
        if len(matching_items) < 2:
            continue
        for item in matching_items:
            _add_reason(findings, item.item_id, "DUPLICATE")


def _apply_split_transactions(
    items: list[ExpenseItemView],
    policy: CompanyPolicy,
    findings: dict[str, list[str]],
) -> None:
    grouped_items: dict[tuple[str, str], list[ExpenseItemView]] = defaultdict(list)
    for item in items:
        grouped_items[(item.submitted_category, item.merchant_descriptor)].append(item)

    for (category, _merchant), matching_items in grouped_items.items():
        threshold = policy.split_transaction_total_threshold_by_category.get(category)
        if threshold is None or len(matching_items) < 2:
            continue

        sorted_items = sorted(
            matching_items,
            key=lambda item: datetime.fromisoformat(item.date_time),
        )
        left = 0
        running_total = 0.0

        for right, current in enumerate(sorted_items):
            current_time = datetime.fromisoformat(current.date_time)
            running_total += current.submitted_amount

            while left <= right:
                left_time = datetime.fromisoformat(sorted_items[left].date_time)
                elapsed_hours = (current_time - left_time).total_seconds() / 3600.0
                if elapsed_hours <= policy.split_transaction_window_hours:
                    break
                running_total -= sorted_items[left].submitted_amount
                left += 1

            if right - left + 1 >= 2 and running_total > threshold:
                for flagged in sorted_items[left : right + 1]:
                    _add_reason(findings, flagged.item_id, "SPLIT_TRANSACTION")
