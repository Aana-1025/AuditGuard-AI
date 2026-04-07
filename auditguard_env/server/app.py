# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""FastAPI server for AuditGuard with a persistent in-memory environment."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import re
from threading import Lock
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from models import AuditGuardAction, AuditGuardObservation
from server.auditguard_environment import AuditGuardEnvironment


app = FastAPI(title="AuditGuard API")

# For cookie/session-style browser calls, origins must be explicit when
# allow_credentials=True.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


env: AuditGuardEnvironment | None = None
_env_lock = Lock()


class ResetRequest(BaseModel):
    seed: int | None = None
    scenario: str | None = None


class StepRequest(BaseModel):
    action: AuditGuardAction


def get_risk_label(score: Any) -> str:
    try:
        numeric_score = float(score or 0.0)
    except (TypeError, ValueError):
        numeric_score = 0.0
    if numeric_score < 0.3:
        return "LOW"
    if numeric_score < 0.8:
        return "MEDIUM"
    return "HIGH"


def get_decision_hint(risk_score: float) -> str:
    if risk_score >= 0.8:
        return "FLAG"
    if risk_score >= 0.6:
        return "REVIEW"
    return "APPROVE"


def generate_ai_explanations(observation: dict[str, Any]) -> dict[str, dict[str, Any]]:
    items = observation.get("items", [])
    company_policy = observation.get("company_policy", {}) or {}
    risk_by_item = observation.get("risk_by_item", {}) or {}
    item_status = observation.get("item_status", {}) or {}

    forbidden_merchants = set(company_policy.get("forbidden_merchants", []))
    receipt_threshold = float(company_policy.get("receipt_required_over_amount", 0.0))
    split_window_hours = float(company_policy.get("split_transaction_window_hours", 0))

    items_by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        category = str(item.get("submitted_category", ""))
        items_by_category[category].append(item)

    split_flag_items: set[str] = set()
    for category_items in items_by_category.values():
        sorted_items = sorted(
            category_items,
            key=lambda x: str(x.get("date_time", "")),
        )
        for i in range(len(sorted_items)):
            left = sorted_items[i]
            left_time_raw = left.get("date_time")
            if not left_time_raw:
                continue
            try:
                left_time = datetime.fromisoformat(str(left_time_raw))
            except ValueError:
                continue
            for j in range(i + 1, len(sorted_items)):
                right = sorted_items[j]
                right_time_raw = right.get("date_time")
                if not right_time_raw:
                    continue
                try:
                    right_time = datetime.fromisoformat(str(right_time_raw))
                except ValueError:
                    continue
                delta_hours = abs((right_time - left_time).total_seconds()) / 3600.0
                if delta_hours <= split_window_hours:
                    left_id = str(left.get("item_id", ""))
                    right_id = str(right.get("item_id", ""))
                    if left_id:
                        split_flag_items.add(left_id)
                    if right_id:
                        split_flag_items.add(right_id)

    explanations: dict[str, dict[str, Any]] = {}
    for item in items:
        item_id = str(item.get("item_id", ""))
        amount = float(item.get("submitted_amount", 0.0) or 0.0)
        merchant = str(item.get("merchant_descriptor", ""))
        receipt_present = bool(item.get("receipt_present", False))
        risk_score = float(risk_by_item.get(item_id, 0.0) or 0.0)

        reasons: list[str] = []

        if merchant in forbidden_merchants:
            reasons.append("Merchant is on the company forbidden list.")
        if (not receipt_present) and amount > receipt_threshold:
            reasons.append(
                "Receipt is missing for an amount that exceeds policy receipt threshold."
            )
        if item_id in split_flag_items:
            reasons.append(
                "Multiple transactions in the same category occurred within the split-transaction time window."
            )

        severity = get_risk_label(risk_score)

        decision_hint = get_decision_hint(risk_score)
        print(f"DEBUG \u2192 risk_score={risk_score}, decision_hint={decision_hint}")

        user_action = None
        status = str(item_status.get(item_id, "")).strip().lower()
        if status == "flagged":
            user_action = "FLAG"
        elif status == "approved":
            user_action = "APPROVE"
        elif status == "needs_info":
            user_action = "REVIEW"

        if user_action is not None and user_action != decision_hint:
            reasons.append("User decision differs from AI recommendation.")

        explanations[item_id] = {
            "risk_score": risk_score,
            "severity": severity,
            "reasons": reasons,
            "decision_hint": decision_hint,
            "user_action": user_action,
        }

    return explanations


def enrich_observation_with_ai_explanations(
    observation: dict[str, Any],
) -> dict[str, Any]:
    ai_explanations = generate_ai_explanations(observation)
    observation["ai_explanations"] = ai_explanations

    correct_ai_decisions = 0
    incorrect_ai_decisions = 0
    compared_decisions = 0
    for explanation in ai_explanations.values():
        decision_hint = explanation.get("decision_hint")
        user_action = explanation.get("user_action")
        if not user_action:
            continue
        compared_decisions += 1
        if user_action == decision_hint:
            correct_ai_decisions += 1
        else:
            incorrect_ai_decisions += 1

    observation["ai_decision_stats"] = {
        "correct_ai_decisions": correct_ai_decisions,
        "incorrect_ai_decisions": incorrect_ai_decisions,
        "compared_decisions": compared_decisions,
    }

    enriched_items: list[dict[str, Any]] = []
    for index, item in enumerate(observation.get("items", [])):
        item_id = (
            item.get("item_id")
            or item.get("id")
            or item.get("expense_id")
            or f"item-{index}"
        )
        explanation = ai_explanations.get(str(item_id), {})
        enriched_items.append(
            {
                **item,
                "explanation": explanation,
                "decision_hint": explanation.get("decision_hint", "N/A"),
                "reasons": explanation.get("reasons", []),
            }
        )

    observation["items"] = enriched_items
    return observation


@app.post("/reset")
def reset(payload: ResetRequest | None = None):
    global env

    with _env_lock:
        env = AuditGuardEnvironment()
        kwargs: dict[str, object] = {}
        if payload is not None:
            if payload.seed is not None:
                kwargs["seed"] = payload.seed
            if payload.scenario is not None:
                kwargs["scenario"] = payload.scenario
        observation = env.reset(**kwargs)
        observation_dict = observation.model_dump(mode="json")
        observation_dict = enrich_observation_with_ai_explanations(observation_dict)
        env_id = id(env)
        print("ENV ID:", env_id)

    return {"observation": observation_dict}


@app.post("/step")
def step(payload: StepRequest):
    global env

    def parse_action(raw_message: str) -> tuple[str, str, str | None]:
        text = (raw_message or "").strip()
        match = re.fullmatch(
            r"(approve|flag)\s+item\s+(EXP-\d{3})(?:\s+as\s+([A-Z_]+))?",
            text,
            flags=re.IGNORECASE,
        )
        if not match:
            raise ValueError(
                "Invalid action format. Use exactly: 'approve item EXP-001' or 'flag item EXP-001 as REASON_CODE'."
            )
        action_name = match.group(1).upper()
        item_id = match.group(2).upper()
        reason_code = match.group(3).upper() if match.group(3) else None
        return action_name, item_id, reason_code

    def update_stats(observation_dict: dict[str, Any]) -> tuple[int, int, int]:
        ai_explanations = observation_dict.get("ai_explanations", {}) or {}
        item_status = observation_dict.get("item_status", {}) or {}

        correct_ai_decisions = 0
        incorrect_ai_decisions = 0
        compared_decisions = 0

        for item_id, explanation in ai_explanations.items():
            decision_hint = str(explanation.get("decision_hint", "")).upper()
            status = str(item_status.get(item_id, "")).lower()
            user_action = None
            if status == "approved":
                user_action = "APPROVE"
            elif status == "flagged":
                user_action = "FLAG"

            explanation["user_action"] = user_action
            if user_action is None:
                continue

            compared_decisions += 1
            if user_action == decision_hint:
                correct_ai_decisions += 1
            else:
                incorrect_ai_decisions += 1
                reasons = explanation.get("reasons", [])
                if isinstance(reasons, list) and "User decision differs from AI recommendation." not in reasons:
                    reasons.append("User decision differs from AI recommendation.")
                    explanation["reasons"] = reasons

        observation_dict["ai_decision_stats"] = {
            "correct_ai_decisions": correct_ai_decisions,
            "incorrect_ai_decisions": incorrect_ai_decisions,
            "compared_decisions": compared_decisions,
        }
        return correct_ai_decisions, incorrect_ai_decisions, compared_decisions

    def compute_final_score(correct_ai_decisions: int, incorrect_ai_decisions: int) -> float:
        return float((correct_ai_decisions * 10) - (incorrect_ai_decisions * 5))

    def apply_action(
        action_name: str,
        item_id: str,
        reason_code: str | None,
        decision_hint: str,
    ) -> float:
        user_action = "APPROVE" if action_name == "APPROVE" else "FLAG"
        is_correct = user_action == decision_hint
        reward = 1.0 if is_correct else -1.0

        # Mutate only targeted item status.
        if user_action == "APPROVE":
            env._item_status[item_id] = "approved"
            if item_id not in env._state.approvals:
                env._state.approvals.append(item_id)
        else:
            env._item_status[item_id] = "flagged"
            if item_id not in env.flagged_items:
                env.flagged_items.add(item_id)
            if item_id in env.fraud_ground_truth:
                env.correct_flagged_items.add(item_id)
            if not any(flag.startswith(f"{item_id}:") for flag in env._state.flags):
                env._state.flags.append(f"{item_id}:{reason_code or 'MANUAL_FLAG'}")

        print("UPDATED STATUS:", env._item_status)

        env._state.step_count += 1
        env._state.actions_taken += 1
        env.audit_budget_remaining = max(0, env.audit_budget_remaining - 1)
        env._state.audit_budget_remaining = env.audit_budget_remaining
        return reward

    with _env_lock:
        if env is None:
            raise HTTPException(status_code=400, detail="Environment not initialized")

        if env._scenario is None:
            env.reset(scenario="easy", seed=0)

        scenario_data = env._scenario

        if env.audit_budget_remaining <= 0:
            env._state.last_action_error = "Audit budget exhausted"
            obs = env._build_observation(
                scenario_data,
                messages=[env._state.last_action_error],
                done=True,
                reward=0.0,
                final_report=env._final_report,
                final_score=env._state.final_score,
            )
            observation_dict = obs.model_dump(mode="json")
            observation_dict = enrich_observation_with_ai_explanations(observation_dict)
            correct, incorrect, compared = update_stats(observation_dict)
            observation_dict["accuracy"] = (correct / compared) if compared else 0.0
            observation_dict["reviewed_items"] = compared
            env_id = id(env)
            print("ENV ID:", env_id)
            return {"observation": observation_dict}

        base_obs = env._build_observation(
            scenario_data,
            messages=[],
            done=False,
            reward=0.0,
            final_report=env._final_report,
            final_score=env._state.final_score,
        )
        base_observation_dict = base_obs.model_dump(mode="json")
        base_observation_dict = enrich_observation_with_ai_explanations(base_observation_dict)

        try:
            action_name, item_id, reason_code = parse_action(payload.action.message)
        except ValueError as exc:
            env._state.last_action_error = str(exc)
            observation = env._build_observation(
                scenario_data,
                messages=[env._state.last_action_error],
                done=False,
                reward=0.0,
                final_report=env._final_report,
                final_score=env._state.final_score,
            )
            observation_dict = observation.model_dump(mode="json")
            observation_dict = enrich_observation_with_ai_explanations(observation_dict)
            correct, incorrect, compared = update_stats(observation_dict)
            observation_dict["accuracy"] = (correct / compared) if compared else 0.0
            observation_dict["reviewed_items"] = compared
            env_id = id(env)
            print("ENV ID:", env_id)
            return {"observation": observation_dict}

        if item_id not in env._item_status:
            env._state.last_action_error = f"Invalid item id: {item_id}"
            observation = env._build_observation(
                scenario_data,
                messages=[env._state.last_action_error],
                done=False,
                reward=0.0,
                final_report=env._final_report,
                final_score=env._state.final_score,
            )
            observation_dict = observation.model_dump(mode="json")
            observation_dict = enrich_observation_with_ai_explanations(observation_dict)
            correct, incorrect, compared = update_stats(observation_dict)
            observation_dict["accuracy"] = (correct / compared) if compared else 0.0
            observation_dict["reviewed_items"] = compared
            env_id = id(env)
            print("ENV ID:", env_id)
            return {"observation": observation_dict}

        if env._item_status.get(item_id) != "unreviewed":
            env._state.last_action_error = f"Item already reviewed: {item_id}"
            observation = env._build_observation(
                scenario_data,
                messages=[env._state.last_action_error],
                done=False,
                reward=0.0,
                final_report=env._final_report,
                final_score=env._state.final_score,
            )
            observation_dict = observation.model_dump(mode="json")
            observation_dict = enrich_observation_with_ai_explanations(observation_dict)
            correct, incorrect, compared = update_stats(observation_dict)
            observation_dict["accuracy"] = (correct / compared) if compared else 0.0
            observation_dict["reviewed_items"] = compared
            env_id = id(env)
            print("ENV ID:", env_id)
            return {"observation": observation_dict}

        explanation = base_observation_dict.get("ai_explanations", {}).get(item_id, {})
        decision_hint = str(explanation.get("decision_hint", "APPROVE")).upper()
        reward = apply_action(action_name, item_id, reason_code, decision_hint)

        done = env.audit_budget_remaining <= 0
        messages = [f"{action_name.title()} recorded for {item_id}.", f"Budget remaining: {env.audit_budget_remaining}"]

        final_score = env._state.final_score
        if done:
            env._state.finalised = True

        observation = env._build_observation(
            scenario_data,
            messages=messages,
            done=done,
            reward=reward,
            final_report=env._final_report,
            final_score=final_score,
        )
        observation_dict = observation.model_dump(mode="json")
        observation_dict["item_statuses"] = {
            **(observation_dict.get("item_status", {}) or {})
        }
        observation_dict = enrich_observation_with_ai_explanations(observation_dict)

        correct_ai_decisions, incorrect_ai_decisions, compared_decisions = update_stats(
            observation_dict
        )
        observation_dict["accuracy"] = (
            correct_ai_decisions / compared_decisions if compared_decisions else 0.0
        )
        observation_dict["reviewed_items"] = compared_decisions

        if done:
            final_score = compute_final_score(correct_ai_decisions, incorrect_ai_decisions)
            env._state.final_score = final_score
            observation_dict["final_score"] = final_score

        env_id = id(env)
        print("ENV ID:", env_id)

    return {"observation": observation_dict}


@app.get("/state")
def state():
    global env

    with _env_lock:
        if env is None:
            raise HTTPException(status_code=400, detail="Environment not initialized")
        return {"state": env.state.model_dump(mode="json")}


@app.get("/schema")
def schema():
    return {
        "action_schema": AuditGuardAction.model_json_schema(),
        "observation_schema": AuditGuardObservation.model_json_schema(),
    }


def main(host: str = "0.0.0.0", port: int = 8000):
    import uvicorn

    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    main(port=args.port)
