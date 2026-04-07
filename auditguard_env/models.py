# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Data models for the AuditGuard environment."""

from typing import Any, Literal

from openenv.core.env_server.types import Action, Observation, State
from pydantic import BaseModel, ConfigDict, Field


class AuditGuardAction(Action):
    """Action model for audit interactions and finalisation."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    kind: Literal["instruction", "finalise"] = Field(
        default="instruction",
        description="Action kind. Use 'finalise' to close and score the episode.",
    )
    message: str = Field(
        default="",
        description="Action text payload for instruction mode.",
    )


class CompanyPolicy(BaseModel):
    """Company expense policy exposed to the agent."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    receipt_required_over_amount: float = Field(..., ge=0)
    caps_by_category: dict[str, float] = Field(default_factory=dict)
    forbidden_merchants: list[str] = Field(default_factory=list)
    blocked_mccs: list[str] = Field(default_factory=list)
    required_fields: list[str] = Field(default_factory=list)
    split_transaction_window_hours: int = Field(..., ge=1)
    split_transaction_total_threshold_by_category: dict[str, float] = Field(
        default_factory=dict
    )


class ExpenseItemView(BaseModel):
    """Only agent-visible expense fields."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    item_id: str = Field(...)
    submitted_category: str = Field(...)
    submitted_amount: float = Field(..., ge=0)
    currency: str = Field(...)
    date_time: str = Field(...)
    merchant_descriptor: str = Field(...)
    receipt_present: bool = Field(...)
    receipt_id: str | None = Field(default=None)
    receipt_total: float | None = Field(default=None, ge=0)
    receipt_hash: str | None = Field(default=None)
    merchant_mcc: str = Field(...)
    employee_note: str = Field(...)


class FinalReport(BaseModel):
    """Final deterministic audit summary generated on finalisation."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    true_positives: int = Field(..., ge=0)
    false_positives: int = Field(..., ge=0)
    false_negatives: int = Field(..., ge=0)
    missed_fraud: int = Field(..., ge=0)
    hard_fraud_caught: int = Field(..., ge=0)
    hard_fraud_total: int = Field(..., ge=0)
    report_decision_correct: bool = Field(...)
    final_score: float = Field(..., ge=0, le=1)


class AuditGuardObservation(Observation):
    """Observation model for scenario-driven audit tasks."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    scenario: str = Field(...)
    task_brief: str = Field(...)
    company_policy: CompanyPolicy = Field(...)
    items: list[ExpenseItemView] = Field(default_factory=list)
    item_status: dict[str, str] = Field(default_factory=dict)
    audit_budget_remaining: int = Field(..., ge=0)
    audit_budget_total: int = Field(..., ge=0)
    risk_overall: float = Field(..., ge=0, le=1)
    risk_by_item: dict[str, float] = Field(default_factory=dict)
    allowed_reason_codes: list[str] = Field(default_factory=list)
    messages: list[str] = Field(default_factory=list)
    final_report: FinalReport | None = Field(default=None)
    final_score: float | None = Field(default=None, ge=0, le=1)


class AuditGuardState(State):
    """Internal state model for the environment."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    episode_id: str = Field(...)
    step_count: int = Field(..., ge=0)
    scenario: str = Field(...)
    seed: int = Field(...)
    audit_budget_remaining: int = Field(..., ge=0)
    actions_taken: int = Field(default=0, ge=0)
    flags: list[str] = Field(default_factory=list)
    approvals: list[str] = Field(default_factory=list)
    info_requests: list[str] = Field(default_factory=list)
    finalised: bool = Field(default=False)
    final_score: float | None = Field(default=None)
    last_action_error: str | None = Field(default=None)


class ScenarioInstance(BaseModel):
    """Generated scenario package used by the environment reset path."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    scenario: str = Field(...)
    seed: int = Field(...)
    task_brief: str = Field(...)
    company_policy: CompanyPolicy = Field(...)
    items: list[ExpenseItemView] = Field(default_factory=list)
    audit_budget_total: int = Field(..., ge=0)
    allowed_reason_codes: list[str] = Field(default_factory=list)
    hidden: dict[str, Any] = Field(default_factory=dict)


# Backward-compatible aliases
AuditguardAction = AuditGuardAction
AuditguardObservation = AuditGuardObservation
AuditguardState = AuditGuardState
