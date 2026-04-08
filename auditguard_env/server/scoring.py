"""Continuous scoring utilities for AuditGuard."""

from __future__ import annotations

from dataclasses import dataclass

from server.models import ExpenseItemView


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _risk_multiplier(risk_score: float) -> float:
    risk_value = float(risk_score)
    if risk_value > 0.8:
        return 2.0
    if risk_value > 0.5:
        return 1.5
    return 1.0


@dataclass(frozen=True)
class ScoreBreakdownResult:
    correct: int
    wrong_flags: int
    missed_frauds: int
    critical: int


@dataclass(frozen=True)
class ScoreResult:
    final_score: float
    accuracy_percentage: float
    total_items: int
    correct_actions: int
    wrong_flags: int
    missed_frauds: int
    critical_mistakes_count: int
    true_positives: int
    false_positives: int
    false_negatives: int
    hard_fraud_caught: int
    hard_fraud_total: int
    precision: float
    recall: float
    report_decision_correct: bool
    breakdown: ScoreBreakdownResult


def score_audit(
    items: list[ExpenseItemView],
    item_status: dict[str, str],
    fraud_ground_truth: set[str],
    risk_by_item: dict[str, float],
) -> ScoreResult:
    total_items = len(items)
    if total_items == 0:
        empty_breakdown = ScoreBreakdownResult(
            correct=0,
            wrong_flags=0,
            missed_frauds=0,
            critical=0,
        )
        return ScoreResult(
            final_score=1.0,
            accuracy_percentage=100.0,
            total_items=0,
            correct_actions=0,
            wrong_flags=0,
            missed_frauds=0,
            critical_mistakes_count=0,
            true_positives=0,
            false_positives=0,
            false_negatives=0,
            hard_fraud_caught=0,
            hard_fraud_total=0,
            precision=1.0,
            recall=1.0,
            report_decision_correct=True,
            breakdown=empty_breakdown,
        )

    total_score = 0.0
    max_possible_score = 0.0
    correct_actions = 0
    wrong_flags = 0
    missed_frauds = 0
    critical_mistakes_count = 0
    true_positives = 0
    false_positives = 0
    false_negatives = 0

    for item in items:
        item_id = item.item_id
        status = item_status.get(item_id, "unreviewed")
        is_flagged = status == "flagged"
        is_fraud = item_id in fraud_ground_truth
        risk_weight = _risk_multiplier(risk_by_item.get(item_id, 0.0))
        max_possible_score += 1.0 * risk_weight

        if is_fraud and is_flagged:
            base_score = 1.0
            correct_actions += 1
            true_positives += 1
        elif not is_fraud and not is_flagged:
            base_score = 1.0
            correct_actions += 1
        elif is_fraud and not is_flagged:
            base_score = -1.5
            missed_frauds += 1
            critical_mistakes_count += 1
            false_negatives += 1
        else:
            base_score = -0.5
            wrong_flags += 1
            false_positives += 1

        total_score += base_score * risk_weight

    normalized_score = _clamp01(total_score / max_possible_score) if max_possible_score else 1.0
    accuracy_percentage = round((correct_actions / total_items) * 100.0, 2)
    precision = true_positives / (true_positives + false_positives) if (true_positives + false_positives) else 1.0
    recall = true_positives / len(fraud_ground_truth) if fraud_ground_truth else 1.0
    report_decision_correct = (
        correct_actions == total_items and wrong_flags == 0 and missed_frauds == 0
    )

    breakdown = ScoreBreakdownResult(
        correct=correct_actions,
        wrong_flags=wrong_flags,
        missed_frauds=missed_frauds,
        critical=critical_mistakes_count,
    )

    return ScoreResult(
        final_score=round(normalized_score, 4),
        accuracy_percentage=accuracy_percentage,
        total_items=total_items,
        correct_actions=correct_actions,
        wrong_flags=wrong_flags,
        missed_frauds=missed_frauds,
        critical_mistakes_count=critical_mistakes_count,
        true_positives=true_positives,
        false_positives=false_positives,
        false_negatives=false_negatives,
        hard_fraud_caught=true_positives,
        hard_fraud_total=len(fraud_ground_truth),
        precision=round(_clamp01(precision), 4),
        recall=round(_clamp01(recall), 4),
        report_decision_correct=report_decision_correct,
        breakdown=breakdown,
    )
