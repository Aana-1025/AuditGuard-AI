"""Deterministic grading utilities for AuditGuard."""

from __future__ import annotations

from dataclasses import dataclass


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _extract_item_id(token: str) -> str:
    return str(token).split(":", 1)[0].strip().upper()


def _normalize_item_ids(values: list[str] | set[str] | tuple[str, ...]) -> set[str]:
    return {_extract_item_id(value) for value in values if str(value).strip()}


def _normalize_ground_truth(
    ground_truth: dict[str, list[str]] | dict[str, object] | set[str] | list[str],
) -> set[str]:
    if isinstance(ground_truth, dict):
        if "fraud_ground_truth" in ground_truth:
            raw_values = ground_truth.get("fraud_ground_truth", [])
            return _normalize_item_ids(list(raw_values))
        return {
            _extract_item_id(item_id)
            for item_id, reason_codes in ground_truth.items()
            if isinstance(reason_codes, list) and reason_codes
        }
    return _normalize_item_ids(list(ground_truth))


def score_flags(
    agent_flags: list[str] | set[str] | tuple[str, ...],
    ground_truth: dict[str, list[str]] | dict[str, object] | set[str] | list[str],
) -> float:
    flagged_items = _normalize_item_ids(agent_flags)
    fraud_items = _normalize_ground_truth(ground_truth)

    if not fraud_items and not flagged_items:
        return 1.0

    true_positives = len(flagged_items & fraud_items)
    false_positives = len(flagged_items - fraud_items)
    missed_fraud = len(fraud_items - flagged_items)

    if not fraud_items:
        return _clamp01(1.0 - (0.3 * false_positives))

    exact_match_bonus = 0.15 if flagged_items == fraud_items else 0.0
    recall = true_positives / len(fraud_items)
    precision_penalty = 0.12 * false_positives
    missed_penalty = 0.3 * missed_fraud

    score = (0.85 * recall) + exact_match_bonus - precision_penalty - missed_penalty
    return _clamp01(score)


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
    ground_truth: dict[str, list[str]] | dict[str, object] | set[str] | list[str],
) -> GradeResult:
    flagged_items = _normalize_item_ids(actions_taken)
    approved_items = _normalize_item_ids(approvals)
    fraud_items = _normalize_ground_truth(ground_truth)

    true_positives = len(flagged_items & fraud_items)
    false_positives = len(flagged_items - fraud_items)
    false_negatives = len(fraud_items - flagged_items)
    bad_approvals = len(approved_items & fraud_items)

    total_actual_fraud = len(fraud_items)
    total_flagged = len(flagged_items)
    precision = true_positives / total_flagged if total_flagged else 0.0
    recall = true_positives / total_actual_fraud if total_actual_fraud else 1.0
    report_decision_correct = (
        flagged_items == fraud_items and bad_approvals == 0 and bool(final_decision)
    )

    final_score = score_flags(flagged_items, ground_truth)
    if bad_approvals:
        final_score = _clamp01(final_score - (0.2 * bad_approvals))

    return GradeResult(
        true_positives=true_positives,
        false_positives=false_positives,
        false_negatives=false_negatives,
        missed_fraud=false_negatives,
        total_actual_fraud=total_actual_fraud,
        total_flagged=total_flagged,
        hard_fraud_caught=true_positives,
        hard_fraud_total=total_actual_fraud,
        report_decision_correct=report_decision_correct,
        precision=_clamp01(precision),
        recall=_clamp01(recall),
        final_score=_clamp01(final_score),
    )


def compute_progress(
    actions_taken_partial: list[str],
    ground_truth: dict[str, list[str]] | dict[str, object] | set[str] | list[str],
) -> float:
    return score_flags(actions_taken_partial, ground_truth)
