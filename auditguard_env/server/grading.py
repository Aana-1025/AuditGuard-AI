"""Deterministic grading utilities for AuditGuard."""

from __future__ import annotations

from dataclasses import dataclass


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _extract_item_id(token: str) -> str:
    return str(token).split(":", 1)[0].strip().upper()


def _normalize_item_ids(values: list[str] | set[str] | tuple[str, ...]) -> set[str]:
    return {_extract_item_id(v) for v in values if str(v).strip()}


@dataclass(frozen=True)
class GradeResult:
    true_positives: int
    false_positives: int
    false_negatives: int
    missed_fraud: int
    total_actual_fraud: int
    total_flagged: int
    hard_fraud_caught: int
    hard_fraud_total: int
    report_decision_correct: bool
    precision: float
    recall: float
    final_score: float


def grade_episode(
    actions_taken: list[str],
    approvals: list[str],
    final_decision: bool | None,
    ground_truth: dict[str, object] | set[str] | list[str],
) -> GradeResult:
    if isinstance(ground_truth, dict):
        fraud_ground_truth = _normalize_item_ids(
            list(ground_truth.get("fraud_ground_truth", []))
        )
        hard_fraud_items = _normalize_item_ids(
            list(ground_truth.get("hard_fraud_items", []))
        )
    else:
        fraud_ground_truth = _normalize_item_ids(list(ground_truth))
        hard_fraud_items = set()

    flagged = _normalize_item_ids(actions_taken)
    approved = _normalize_item_ids(approvals)

    true_positives = len(flagged.intersection(fraud_ground_truth))
    false_positives = len(flagged.difference(fraud_ground_truth))
    false_negatives = len(fraud_ground_truth.difference(flagged))
    bad_approvals = len(approved.intersection(fraud_ground_truth))

    total_actual_fraud = len(fraud_ground_truth)
    total_flagged = len(flagged)
    hard_fraud_total = len(hard_fraud_items)
    hard_fraud_caught = len(flagged.intersection(hard_fraud_items))

    precision_denom = true_positives + false_positives
    recall_denom = true_positives + false_negatives
    precision = true_positives / precision_denom if precision_denom else 0.0
    recall = true_positives / recall_denom if recall_denom else 0.0
    f1_denom = precision + recall
    f1_score = (2.0 * precision * recall / f1_denom) if f1_denom else 0.0

    fp_rate = false_positives / max(1, total_flagged)
    bad_approval_rate = bad_approvals / max(1, total_actual_fraud)

    base_score = f1_score
    base_score *= 1.0 - (0.55 * fp_rate)
    base_score *= 1.0 - (0.90 * bad_approval_rate)

    hard_bonus = (
        0.20 * (hard_fraud_caught / hard_fraud_total) if hard_fraud_total else 0.0
    )
    final_score = _clamp01(base_score + hard_bonus)

    computed_report_correct = (
        false_negatives == 0 and false_positives == 0 and bad_approvals == 0
    )
    report_decision_correct = computed_report_correct
    if final_decision is not None:
        report_decision_correct = computed_report_correct and bool(final_decision)

    return GradeResult(
        true_positives=true_positives,
        false_positives=false_positives,
        false_negatives=false_negatives,
        missed_fraud=false_negatives,
        total_actual_fraud=total_actual_fraud,
        total_flagged=total_flagged,
        hard_fraud_caught=hard_fraud_caught,
        hard_fraud_total=hard_fraud_total,
        report_decision_correct=report_decision_correct,
        precision=_clamp01(precision),
        recall=_clamp01(recall),
        final_score=_clamp01(final_score),
    )


def compute_progress(
    actions_taken_partial: list[str],
    ground_truth: dict[str, object] | set[str] | list[str],
) -> float:
    approvals: list[str] = []
    final_decision: bool | None = None
    if isinstance(ground_truth, dict):
        approvals = list(ground_truth.get("approvals", []))
        if "final_decision" in ground_truth:
            final_decision = bool(ground_truth["final_decision"])

    graded = grade_episode(
        actions_taken=actions_taken_partial,
        approvals=approvals,
        final_decision=final_decision,
        ground_truth=ground_truth,
    )
    return _clamp01(graded.final_score)


if __name__ == "__main__":
    # Lightweight deterministic self-checks for local validation.
    truth = {
        "fraud_ground_truth": ["EXP-1", "EXP-2"],
        "hard_fraud_items": ["EXP-2"],
        "approvals": [],
    }
    p0 = compute_progress([], truth)
    p1 = compute_progress(["EXP-1:OVER_CAP"], truth)
    p2 = compute_progress(["EXP-1:OVER_CAP", "EXP-2:SPLIT_TRANSACTION"], truth)
    assert 0.0 <= p0 <= p1 <= p2 <= 1.0

    grade_bad = grade_episode(
        actions_taken=["EXP-1:OVER_CAP"],
        approvals=["EXP-2"],
        final_decision=True,
        ground_truth=truth,
    )
    grade_clean = grade_episode(
        actions_taken=["EXP-1:OVER_CAP", "EXP-2:SPLIT_TRANSACTION"],
        approvals=[],
        final_decision=True,
        ground_truth=truth,
    )
    assert 0.0 <= grade_bad.final_score <= 1.0
    assert 0.0 <= grade_clean.final_score <= 1.0
    assert grade_bad.final_score <= grade_clean.final_score
