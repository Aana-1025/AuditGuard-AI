from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StrictBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class AuditGuardAction(StrictBaseModel):
    action_type: str
    item_id: str | None = None
    reason_code: str | None = None
    note: str | None = None
    message: str = Field(default="")


class CompanyPolicy(StrictBaseModel):
    receipt_required_over_amount: float = Field(..., ge=0)
    caps_by_category: dict[str, float] = Field(default_factory=dict)
    forbidden_merchants: list[str] = Field(default_factory=list)
    blocked_mccs: list[str] = Field(default_factory=list)
    required_fields: list[str] = Field(default_factory=list)
    split_transaction_window_hours: int = Field(..., ge=1)
    split_transaction_total_threshold_by_category: dict[str, float] = Field(
        default_factory=dict
    )


class ExpenseItemView(StrictBaseModel):
    item_id: str
    submitted_category: str
    submitted_amount: float = Field(..., ge=0)
    currency: str
    date_time: str
    merchant_descriptor: str
    receipt_present: bool
    receipt_id: str | None = None
    receipt_total: float | None = Field(default=None, ge=0)
    receipt_hash: str | None = None
    merchant_mcc: str
    employee_note: str


class ScoringBreakdown(StrictBaseModel):
    correct: int = Field(..., ge=0)
    wrong_flags: int = Field(..., ge=0)
    missed_frauds: int = Field(..., ge=0)
    critical: int = Field(..., ge=0)


class FinalReport(StrictBaseModel):
    true_positives: int = Field(..., ge=0)
    false_positives: int = Field(..., ge=0)
    false_negatives: int = Field(..., ge=0)
    missed_fraud: int = Field(..., ge=0)
    hard_fraud_caught: int = Field(..., ge=0)
    hard_fraud_total: int = Field(..., ge=0)
    report_decision_correct: bool
    final_score: float = Field(..., ge=0, le=1)
    accuracy_percentage: float = Field(..., ge=0, le=100)
    total_items: int = Field(..., ge=0)
    correct_actions: int = Field(..., ge=0)
    wrong_flags: int = Field(..., ge=0)
    critical_mistakes_count: int = Field(..., ge=0)
    breakdown: ScoringBreakdown


class AuditGuardObservation(StrictBaseModel):
    scenario: str
    task_brief: str
    company_policy: CompanyPolicy
    items: list[ExpenseItemView] = Field(default_factory=list)
    item_status: dict[str, str] = Field(default_factory=dict)
    audit_budget_remaining: int = Field(..., ge=0)
    audit_budget_total: int = Field(..., ge=0)
    risk_overall: float = Field(..., ge=0, le=1)
    risk_by_item: dict[str, float] = Field(default_factory=dict)
    allowed_reason_codes: list[str] = Field(default_factory=list)
    messages: list[str] = Field(default_factory=list)
    done: bool = False
    reward: float = Field(default=0.0, ge=0, le=1)
    final_report: FinalReport | None = None
    final_score: float | None = Field(default=None, ge=0, le=1)
    accuracy: float | None = Field(default=None, ge=0, le=100)
    breakdown: ScoringBreakdown | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AuditGuardState(StrictBaseModel):
    episode_id: str
    step_count: int = Field(..., ge=0)
    scenario: str
    seed: int
    audit_budget_remaining: int = Field(..., ge=0)
    actions_taken: int = Field(default=0, ge=0)
    flags: list[str] = Field(default_factory=list)
    approvals: list[str] = Field(default_factory=list)
    info_requests: list[str] = Field(default_factory=list)
    finalised: bool = False
    final_score: float | None = None
    last_action_error: str | None = None


class ScenarioInstance(StrictBaseModel):
    scenario: str
    seed: int
    task_brief: str
    company_policy: CompanyPolicy
    items: list[ExpenseItemView] = Field(default_factory=list)
    audit_budget_total: int = Field(..., ge=0)
    allowed_reason_codes: list[str] = Field(default_factory=list)
    hidden: dict[str, Any] = Field(default_factory=dict)


AuditguardAction = AuditGuardAction
AuditguardObservation = AuditGuardObservation
AuditguardState = AuditGuardState
