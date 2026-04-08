from __future__ import annotations

from threading import Lock
from fastapi import Body
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict

from server.auditguard_environment import AuditGuardEnvironment
from server.models import (
    AuditGuardAction,
    AuditGuardObservation,
    AuditGuardState,
    ScoringBreakdown,
)


app = FastAPI(title="AuditGuard API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_env_lock = Lock()
_env = AuditGuardEnvironment()


class ResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seed: int | None = None
    scenario: str | None = None


class StepActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_type: str | None = None
    item_id: str | None = None
    reason_code: str | None = None
    note: str | None = None
    message: str | None = None

    def to_action(self) -> AuditGuardAction:
        raw_action_type = (self.action_type or "").strip()
        normalized = raw_action_type.lower().replace(" ", "_")
        message = self.message or ""

        if normalized in {"", "instruction"}:
            return AuditGuardAction(action_type="instruction", message=message)
        if normalized == "finalise":
            return AuditGuardAction(
                action_type="finalise",
                message="finalise report",
            )
        if normalized == "auto_audit":
            return AuditGuardAction(
                action_type="auto_audit",
                message="auto audit",
            )
        if normalized == "approve":
            if self.item_id:
                return AuditGuardAction(
                    action_type="approve",
                    item_id=self.item_id,
                    message=f"approve {self.item_id}",
                )
            return AuditGuardAction(action_type="approve", message=raw_action_type)
        if normalized == "flag":
            if self.item_id and self.reason_code:
                return AuditGuardAction(
                    action_type="flag",
                    item_id=self.item_id,
                    reason_code=self.reason_code,
                    message=f"flag item {self.item_id} as {self.reason_code}"
                )
            if self.item_id:
                return AuditGuardAction(
                    action_type="flag",
                    item_id=self.item_id,
                    message=f"flag item {self.item_id}",
                )
            return AuditGuardAction(action_type="flag", message=raw_action_type)
        if normalized == "request_info":
            if self.item_id and self.note:
                return AuditGuardAction(
                    action_type="request_info",
                    item_id=self.item_id,
                    note=self.note,
                    message=f"request info for {self.item_id} {self.note}"
                )
            if self.item_id:
                return AuditGuardAction(
                    action_type="request_info",
                    item_id=self.item_id,
                    message=f"request info for {self.item_id}",
                )
            return AuditGuardAction(action_type="request_info", message=raw_action_type)

        return AuditGuardAction(
            action_type=raw_action_type or "instruction",
            item_id=self.item_id,
            reason_code=self.reason_code,
            note=self.note,
            message=message or raw_action_type,
        )


class StepRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_type: str | None = None
    item_id: str | None = None
    reason_code: str | None = None
    note: str | None = None
    message: str | None = None
    action: StepActionRequest | AuditGuardAction | None = None

    def to_action(self) -> AuditGuardAction:
        if isinstance(self.action, AuditGuardAction):
            return self.action
        if isinstance(self.action, StepActionRequest):
            return self.action.to_action()

        return StepActionRequest(
            action_type=self.action_type,
            item_id=self.item_id,
            reason_code=self.reason_code,
            note=self.note,
            message=self.message,
        ).to_action()


class StepResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observation: AuditGuardObservation
    reward: float
    done: bool
    final_score: float | None = None
    accuracy: float | None = None
    breakdown: ScoringBreakdown | None = None
    total_items: int | None = None
    correct_actions: int | None = None
    wrong_flags: int | None = None
    missed_frauds: int | None = None
    critical_mistakes_count: int | None = None


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "healthy"}


@app.post("/reset", response_model=AuditGuardObservation)
def reset(payload: ResetRequest = Body(default_factory=ResetRequest)) -> AuditGuardObservation:
    kwargs: dict[str, object] = {}
    if payload.seed is not None:
        kwargs["seed"] = payload.seed
    if payload.scenario is not None:
        kwargs["scenario"] = payload.scenario

    with _env_lock:
        return _env.reset(**kwargs)


@app.post("/step", response_model=StepResponse)
def step(payload: StepRequest) -> StepResponse:
    with _env_lock:
        if _env._scenario is None:
            raise HTTPException(status_code=400, detail="Environment not initialized")
        observation = _env.step(payload.to_action())

    final_report = observation.final_report
    return StepResponse(
        observation=observation,
        reward=observation.reward,
        done=observation.done,
        final_score=observation.final_score,
        accuracy=observation.accuracy,
        breakdown=observation.breakdown,
        total_items=final_report.total_items if final_report is not None else None,
        correct_actions=final_report.correct_actions if final_report is not None else None,
        wrong_flags=final_report.wrong_flags if final_report is not None else None,
        missed_frauds=final_report.missed_fraud if final_report is not None else None,
        critical_mistakes_count=(
            final_report.critical_mistakes_count if final_report is not None else None
        ),
    )


@app.post("/finalise", response_model=StepResponse)
def finalise() -> StepResponse:
    with _env_lock:
        if _env._scenario is None:
            raise HTTPException(status_code=400, detail="Environment not initialized")
        observation = _env.step(
            AuditGuardAction(action_type="finalise", message="finalise report")
        )

    final_report = observation.final_report
    return StepResponse(
        observation=observation,
        reward=observation.reward,
        done=observation.done,
        final_score=observation.final_score,
        accuracy=observation.accuracy,
        breakdown=observation.breakdown,
        total_items=final_report.total_items if final_report is not None else None,
        correct_actions=final_report.correct_actions if final_report is not None else None,
        wrong_flags=final_report.wrong_flags if final_report is not None else None,
        missed_frauds=final_report.missed_fraud if final_report is not None else None,
        critical_mistakes_count=(
            final_report.critical_mistakes_count if final_report is not None else None
        ),
    )


@app.get("/state", response_model=AuditGuardState)
def state() -> AuditGuardState:
    with _env_lock:
        if _env._scenario is None:
            raise HTTPException(status_code=400, detail="Environment not initialized")
        return _env.state
