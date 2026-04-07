"""AuditGuard environment with deterministic scenario reset."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from random import Random
from uuid import uuid4

from openenv.core.env_server.interfaces import Environment
from pydantic import ConfigDict, Field

from models import (
    AuditGuardAction,
    AuditGuardObservation,
    AuditGuardState,
    CompanyPolicy,
    ExpenseItemView,
    FinalReport,
    ScenarioInstance,
)
from server.grading import compute_progress, grade_episode
from server.scenario_factory import make_scenario


def normalize_merchant(name):
    if not name:
        return ""
    return name.lower().replace(" ", "").replace("-", "")


class ExtendedFinalReport(FinalReport):
    """Extended final report payload with summary metrics."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    total_actual_fraud: int = Field(..., ge=0)
    total_flagged: int = Field(..., ge=0)
    summary: str = Field(...)
    precision: float = Field(..., ge=0, le=1)
    recall: float = Field(..., ge=0, le=1)


class AuditGuardEnvironment(Environment):
    """Scenario-driven AuditGuard environment."""

    SUPPORTS_CONCURRENT_SESSIONS: bool = True

    def __init__(self):
        self._state = AuditGuardState(
            episode_id=str(uuid4()),
            step_count=0,
            scenario="easy",
            seed=0,
            audit_budget_remaining=0,
            actions_taken=0,
            flags=[],
            approvals=[],
            info_requests=[],
            finalised=False,
            final_score=None,
            last_action_error=None,
        )
        self._scenario: ScenarioInstance | None = None
        self._item_status: dict[str, str] = {}
        self.duplicate_items: set[str] = set()
        self.split_transaction_items: set[str] = set()
        self.flagged_items: set[str] = set()
        self.correct_flagged_items: set[str] = set()
        self.fraud_ground_truth: set[str] = set()
        self.fraud_reasons_by_item: dict[str, list[str]] = {}
        self.cumulative_score: float = 0.0
        self.progress_score: float = 0.0
        self._final_report: ExtendedFinalReport | None = None

    @staticmethod
    def _clamp01(value: float) -> float:
        return max(0.0, min(1.0, value))

    def _grading_ground_truth(self) -> dict[str, object]:
        return {
            "fraud_ground_truth": sorted(self.fraud_ground_truth),
            "hard_fraud_items": sorted(self.split_transaction_items),
            "approvals": list(self._state.approvals),
            "total_items": len(self._scenario.items) if self._scenario else 0,
        }

    def _current_progress(self) -> float:
        return self._clamp01(compute_progress(self._state.flags, self._grading_ground_truth()))

    def _progress_reward(self, progress_old: float, progress_new: float) -> float:
        return round(self._clamp01(max(0.0, progress_new - progress_old)), 4)

    def _normalized_binary_reward(self, raw_reward: int) -> float:
        bounded = max(-1, min(1, int(raw_reward)))
        return round(self._clamp01((bounded + 1) / 2), 4)

    def _item_risk(
        self,
        item: ExpenseItemView,
        policy: CompanyPolicy,
        merchant_counts: Counter[str],
        seed: int,
    ) -> float:
        score = 0.05
        fraud_reasons: list[str] = []
        threshold = policy.receipt_required_over_amount
        cap = policy.caps_by_category.get(item.submitted_category)
        is_forbidden_merchant = item.merchant_descriptor in policy.forbidden_merchants

        if item.submitted_amount > threshold and not item.receipt_present:
            score += 0.3
            fraud_reasons.append("MISSING_RECEIPT")
        if threshold - 5.0 <= item.submitted_amount < threshold:
            score += 0.18
        if cap is not None and item.submitted_amount > cap:
            score += 0.28
            fraud_reasons.append("OVER_CAP")
        if merchant_counts[item.merchant_descriptor] > 1:
            score += 0.12
        if is_forbidden_merchant:
            score += 0.6
            fraud_reasons.append("FORBIDDEN_MERCHANT")
        if item.merchant_mcc in policy.blocked_mccs:
            score += 0.25

        mcc_to_cat = {
            "4511": "travel",
            "7011": "lodging",
            "4121": "ground_transport",
            "5732": "office_supplies",
            "5812": "meals",
            "5734": "software",
            "8299": "training",
            "6051": "financial",
            "4829": "financial",
        }
        expected = mcc_to_cat.get(item.merchant_mcc)
        if expected and expected not in (item.submitted_category, "financial"):
            score += 0.2

        if item.receipt_present and item.receipt_total is not None:
            if abs(item.receipt_total - item.submitted_amount) > 3.0:
                score += 0.15
            # RECEIPT MISMATCH CHECK
            submitted = item.submitted_amount
            receipt = item.receipt_total
            if abs(submitted - receipt) > 5:
                score += 0.4
                fraud_reasons.append("RECEIPT_MISMATCH")

        vague_notes = {"misc", "business", "n/a", "meeting", "client thing", "urgent"}
        if item.employee_note.strip().lower() in vague_notes:
            score += 0.1

        # Split pattern contributes additional risk signal.
        if item.item_id in self.split_transaction_items:
            score += 0.2

        noise_rng = Random(f"{seed}:{item.item_id}")
        score += noise_rng.uniform(-0.03, 0.03)

        # Forbidden merchants should never look benign.
        if is_forbidden_merchant:
            score = max(score, 0.8)

        self.fraud_reasons_by_item[item.item_id] = fraud_reasons
        return round(max(0.0, min(1.0, score)), 4)

    def _detect_duplicate_items(self, items: list[ExpenseItemView]) -> set[str]:
        detected: set[str] = set()
        by_receipt_id: dict[str, list[str]] = {}
        by_receipt_hash: dict[str, list[str]] = {}
        by_merchant_amount: dict[tuple[str, float], list[str]] = {}

        for item in items:
            if item.receipt_id:
                by_receipt_id.setdefault(item.receipt_id, []).append(item.item_id)
            if item.receipt_hash:
                by_receipt_hash.setdefault(item.receipt_hash, []).append(item.item_id)
            key = (item.merchant_descriptor, item.submitted_amount)
            by_merchant_amount.setdefault(key, []).append(item.item_id)

        for groups in (by_receipt_id, by_receipt_hash, by_merchant_amount):
            for ids in groups.values():
                if len(ids) > 1:
                    detected.update(ids)
        return detected

    def _detect_split_transaction_items(
        self,
        items: list[ExpenseItemView],
        policy: CompanyPolicy,
    ) -> set[str]:
        detected: set[str] = set()
        grouped: dict[tuple[str, str], list[ExpenseItemView]] = {}
        window_hours = policy.split_transaction_window_hours

        for item in items:
            grouped.setdefault(
                (item.submitted_category, item.merchant_descriptor), []
            ).append(item)

        for (category, _merchant), group_items in grouped.items():
            cap = policy.caps_by_category.get(category)
            if cap is None or len(group_items) < 2:
                continue

            sorted_items = sorted(
                group_items, key=lambda x: datetime.fromisoformat(x.date_time)
            )
            left = 0
            running_total = 0.0

            for right in range(len(sorted_items)):
                right_item = sorted_items[right]
                right_time = datetime.fromisoformat(right_item.date_time)
                running_total += right_item.submitted_amount

                while left <= right:
                    left_item = sorted_items[left]
                    left_time = datetime.fromisoformat(left_item.date_time)
                    diff_hours = (right_time - left_time).total_seconds() / 3600.0
                    if diff_hours <= window_hours:
                        break
                    running_total -= left_item.submitted_amount
                    left += 1

                if right - left + 1 >= 2 and running_total > cap:
                    for idx in range(left, right + 1):
                        detected.add(sorted_items[idx].item_id)

        return detected

    def _build_observation(
        self,
        scenario_data: ScenarioInstance,
        messages: list[str] | None = None,
        done: bool = False,
        reward: float = 0.0,
        final_report: FinalReport | None = None,
        final_score: float | None = None,
    ) -> AuditGuardObservation:
        merchant_counts = Counter(i.merchant_descriptor for i in scenario_data.items)
        self.detect_fraud_patterns()
        self.fraud_reasons_by_item = {}
        risk_by_item: dict[str, float] = {}
        for item in scenario_data.items:
            risk_by_item[item.item_id] = self._item_risk(
                item, scenario_data.company_policy, merchant_counts, scenario_data.seed
            )

        # GROUP ITEMS BY (merchant + category)
        grouped: defaultdict[tuple[str, str], list[ExpenseItemView]] = defaultdict(list)
        for item in scenario_data.items:
            normalized = normalize_merchant(item.merchant_descriptor)
            key = (normalized, item.submitted_category)
            grouped[key].append(item)

        # CHECK SPLIT FRAUD
        for key, items in grouped.items():
            if len(items) < 2:
                continue

            total = sum(i.submitted_amount for i in items)
            category = key[1]
            threshold = (
                scenario_data.company_policy.split_transaction_total_threshold_by_category
                .get(category, None)
            )

            if threshold and total > threshold:
                for item in items:
                    item_id = item.item_id

                    # increase risk
                    risk_by_item[item_id] = min(
                        1.0, risk_by_item.get(item_id, 0.0) + 0.3
                    )

                    # add reason
                    if item_id not in self.fraud_reasons_by_item:
                        self.fraud_reasons_by_item[item_id] = []
                    self.fraud_reasons_by_item[item_id].append("SPLIT_TRANSACTION")

        self.risk_by_item = risk_by_item

        for item_id in self.duplicate_items:
            if item_id in risk_by_item:
                risk_by_item[item_id] = round(min(0.99, risk_by_item[item_id] + 0.2), 4)
        for item_id in self.split_transaction_items:
            if item_id in risk_by_item:
                risk_by_item[item_id] = round(min(0.99, risk_by_item[item_id] + 0.2), 4)

        avg_risk = sum(risk_by_item.values()) / max(1, len(risk_by_item))
        max_risk = max(risk_by_item.values()) if risk_by_item else 0.0
        risk_overall = round(min(1.0, avg_risk * 0.8 + max_risk * 0.2), 4)
        if self.duplicate_items:
            risk_overall = round(min(1.0, risk_overall + 0.03), 4)
        if self.split_transaction_items:
            risk_overall = round(min(1.0, risk_overall + 0.03), 4)
        budget_total = scenario_data.audit_budget_total

        return AuditGuardObservation(
            scenario=scenario_data.scenario,
            task_brief=scenario_data.task_brief,
            company_policy=scenario_data.company_policy,
            items=scenario_data.items,
            item_status={
                item.item_id: self._item_status.get(item.item_id, "unreviewed")
                for item in scenario_data.items
            },
            audit_budget_remaining=self._state.audit_budget_remaining,
            audit_budget_total=budget_total,
            risk_overall=risk_overall,
            risk_by_item=risk_by_item,
            allowed_reason_codes=scenario_data.allowed_reason_codes,
            messages=messages or ["Scenario generated. Begin audit review."],
            done=done,
            reward=reward,
            final_report=final_report,
            final_score=final_score,
            metadata={},
        )

    def _is_flag_reason_correct(
        self,
        item: ExpenseItemView,
        reason_code: str,
        policy: CompanyPolicy,
    ) -> bool:
        if reason_code == "FORBIDDEN_MERCHANT":
            return item.merchant_descriptor in policy.forbidden_merchants
        if reason_code == "MISSING_RECEIPT":
            return not item.receipt_present
        if reason_code == "OVER_CAP":
            cap = policy.caps_by_category.get(item.submitted_category)
            return cap is not None and item.submitted_amount > cap
        if reason_code == "DUPLICATE":
            return item.item_id in self.duplicate_items
        if reason_code == "SPLIT_TRANSACTION":
            return item.item_id in self.split_transaction_items
        if reason_code == "BLOCKED_MCC":
            return item.merchant_mcc in policy.blocked_mccs
        return False

    def _collect_auto_rule_reasons(
        self,
        item: ExpenseItemView,
        policy: CompanyPolicy,
    ) -> list[str]:
        reasons: list[str] = []
        if item.merchant_descriptor in policy.forbidden_merchants:
            reasons.append("FORBIDDEN_MERCHANT")
        if (
            item.submitted_amount > policy.receipt_required_over_amount
            and not item.receipt_present
        ):
            reasons.append("MISSING_RECEIPT")
        cap = policy.caps_by_category.get(item.submitted_category)
        if cap is not None and item.submitted_amount > cap:
            reasons.append("OVER_CAP")
        if item.merchant_mcc in policy.blocked_mccs:
            reasons.append("BLOCKED_MCC")
        return reasons

    def _auto_priority(self, item: ExpenseItemView, policy: CompanyPolicy) -> int:
        score = 0
        if item.merchant_descriptor in policy.forbidden_merchants:
            score += 70
        if (
            item.submitted_amount > policy.receipt_required_over_amount
            and not item.receipt_present
        ):
            score += 60
        cap = policy.caps_by_category.get(item.submitted_category)
        if cap is not None and item.submitted_amount > cap:
            score += 50
        if item.merchant_mcc in policy.blocked_mccs:
            score += 40
        return score

    def detect_split_transactions(self) -> set[str]:
        split_map: dict[tuple[str, str], list[ExpenseItemView]] = {}
        if self._scenario is None:
            return set()

        for item in self._scenario.items:
            merchant = item.merchant_descriptor
            category = item.submitted_category
            key = (merchant, category)
            if key not in split_map:
                split_map[key] = []
            split_map[key].append(item)

        suspicious: set[str] = set()
        thresholds = (
            self._scenario.company_policy.split_transaction_total_threshold_by_category
        )
        for _key, group in split_map.items():
            if len(group) >= 2:
                if len(group) >= 2:
                    for i in group:
                        suspicious.add(i.item_id)

        return suspicious

    def detect_fraud_patterns(self) -> None:
        self.duplicate_items = set()
        self.split_transaction_items = set()

        if self._scenario is None:
            return

        items = self._scenario.items
        company_policy = self._scenario.company_policy

        # DUPLICATE LOGIC
        seen_by_hash: dict[str, str] = {}
        seen_by_merchant_amount: dict[tuple[str, float], str] = {}

        for item in items:
            if item.receipt_hash:
                if item.receipt_hash in seen_by_hash:
                    self.duplicate_items.add(item.item_id)
                    self.duplicate_items.add(seen_by_hash[item.receipt_hash])
                else:
                    seen_by_hash[item.receipt_hash] = item.item_id

            key = (item.merchant_descriptor, item.submitted_amount)
            if key in seen_by_merchant_amount:
                self.duplicate_items.add(item.item_id)
                self.duplicate_items.add(seen_by_merchant_amount[key])
            else:
                seen_by_merchant_amount[key] = item.item_id

        # SPLIT LOGIC
        window_hours = company_policy.split_transaction_window_hours

        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                item1 = items[i]
                item2 = items[j]

                if item1.merchant_descriptor != item2.merchant_descriptor:
                    continue
                if item1.submitted_category != item2.submitted_category:
                    continue

                t1 = datetime.fromisoformat(item1.date_time)
                t2 = datetime.fromisoformat(item2.date_time)

                if abs((t1 - t2).total_seconds()) > window_hours * 3600:
                    continue

                total = item1.submitted_amount + item2.submitted_amount
                threshold = (
                    company_policy.split_transaction_total_threshold_by_category.get(
                        item1.submitted_category
                    )
                )

                if threshold is not None and total > threshold:
                    self.split_transaction_items.add(item1.item_id)
                    self.split_transaction_items.add(item2.item_id)

    def _parse_action_message(
        self, raw: str
    ) -> tuple[str, str | None, str | None, str | None]:
        text = raw.strip()
        lowered = text.lower()

        if lowered == "finalise report":
            return ("finalise", None, None, None)

        parts_original = text.split()
        parts = [p.lower() for p in parts_original]

        if parts and parts[0] == "approve":
            if len(parts_original) < 2:
                return ("invalid", None, None, None)
            item_id = parts_original[-1].upper()
            return ("approve", item_id, None, None)

        if parts and parts[0] == "flag":
            if len(parts_original) < 3:
                return ("invalid", None, None, None)

            if "item" in parts:
                item_index = parts.index("item")
                if item_index + 1 >= len(parts_original):
                    return ("invalid", None, None, None)
                item_id = parts_original[item_index + 1].upper()
            else:
                if len(parts_original) < 2:
                    return ("invalid", None, None, None)
                item_id = parts_original[1].upper()

            if "as" in parts:
                as_index = parts.index("as")
                if as_index + 1 >= len(parts_original):
                    return ("invalid", None, None, None)
                reason_token = parts_original[as_index + 1]
            else:
                reason_token = parts_original[-1]

            reason_code = reason_token.upper()
            return ("flag", item_id, reason_code, None)

        if (
            len(parts_original) >= 4
            and parts[0] == "request"
            and parts[1] == "info"
            and parts[2] == "for"
        ):
            item_id = parts_original[3].upper()
            note = " ".join(parts_original[4:]).strip() or None
            return ("request_info", item_id, None, note)

        return ("invalid", None, None, None)

    def _compute_final_report(self) -> ExtendedFinalReport:
        graded = grade_episode(
            actions_taken=self._state.flags,
            approvals=self._state.approvals,
            final_decision=True,
            ground_truth=self._grading_ground_truth(),
        )
        final_score = round(self._clamp01(graded.final_score), 4)
        final_report = {
            "true_positives": graded.true_positives,
            "false_positives": graded.false_positives,
            "false_negatives": graded.false_negatives,
            "missed_fraud": graded.missed_fraud,
            "total_actual_fraud": graded.total_actual_fraud,
            "total_flagged": graded.total_flagged,
            "precision": graded.precision,
            "recall": graded.recall,
            "hard_fraud_caught": graded.hard_fraud_caught,
            "hard_fraud_total": graded.hard_fraud_total,
            "report_decision_correct": graded.report_decision_correct,
            "final_score": final_score,
            "summary": (
                "Final report: caught "
                f"{graded.true_positives} of {graded.total_actual_fraud} fraud items, "
                f"flagged {graded.total_flagged} items, {graded.false_positives} false "
                f"positives, score {final_score}."
            ),
        }

        return ExtendedFinalReport(**final_report)

    def reset(self, **kwargs) -> AuditGuardObservation:
        """Reset with deterministic scenario and seed."""
        scenario_raw = kwargs.get("scenario", "easy")
        scenario = str(scenario_raw).strip().lower()
        valid_scenarios = {"easy", "medium", "hard"}
        if scenario not in valid_scenarios:
            scenario = "easy"

        if "seed" in kwargs and kwargs["seed"] is not None:
            seed = int(kwargs["seed"])
        else:
            seed = 0

        try:
            scenario_data = make_scenario(scenario, seed)
        except ValueError:
            scenario_data = make_scenario("easy", seed)
        self._scenario = scenario_data
        self._item_status = {item.item_id: "unreviewed" for item in scenario_data.items}
        self.audit_budget_total = scenario_data.audit_budget_total
        self.audit_budget_remaining = self.audit_budget_total
        self.detect_fraud_patterns()
        self.fraud_ground_truth = set(self.duplicate_items) | set(
            self.split_transaction_items
        )
        self.flagged_items = set()
        self.correct_flagged_items = set()
        self.cumulative_score = 0.0
        self.progress_score = 0.0
        self._final_report = None
        self._state = AuditGuardState(
            episode_id=str(uuid4()),
            step_count=0,
            scenario=scenario_data.scenario,
            seed=scenario_data.seed,
            audit_budget_remaining=self.audit_budget_remaining,
            actions_taken=0,
            flags=[],
            approvals=[],
            info_requests=[],
            finalised=False,
            final_score=None,
            last_action_error=None,
        )
        return self._build_observation(
            scenario_data,
            messages=[
                f"Loaded scenario '{scenario_data.scenario}' with seed {scenario_data.seed}.",
                f"Audit budget available: {scenario_data.audit_budget_total}.",
            ],
        )

    def step(self, action: AuditGuardAction) -> AuditGuardObservation:  # type: ignore[override]
        """Step handler with idempotent safeguards for duplicate/invalid actions."""
        if self._scenario is None:
            self._scenario = make_scenario("easy", 0)
            self._item_status = {
                item.item_id: "unreviewed" for item in self._scenario.items
            }
            self.audit_budget_total = self._scenario.audit_budget_total
            self.audit_budget_remaining = self.audit_budget_total
            self._state.audit_budget_remaining = self.audit_budget_remaining
            self.detect_fraud_patterns()
        else:
            self.detect_fraud_patterns()
        self.fraud_ground_truth = set(self.duplicate_items) | set(
            self.split_transaction_items
        )

        scenario_data = self._scenario
        text = action.message.strip()
        message = text.lower()
        if action.kind == "finalise" or "finalise report" in message:
            action_type, item_id, reason_code, note = ("finalise", None, None, None)
        elif message == "auto audit":
            action_type, item_id, reason_code, note = ("auto_audit", None, None, None)
        else:
            action_type, item_id, reason_code, note = self._parse_action_message(text)

        valid_ids = {item.item_id for item in scenario_data.items}

        # Early return: already finalised, no mutation.
        if self._state.finalised:
            self._state.last_action_error = "Episode is already finalised."
            return self._build_observation(
                scenario_data,
                messages=[self._state.last_action_error],
                done=True,
                reward=0.0,
                final_report=self._final_report,
                final_score=self._state.final_score,
            )

        # Early return: invalid format, unknown item, duplicate item interaction.
        if action_type == "invalid":
            self._state.last_action_error = (
                "Invalid action format. Use: 'flag item EXP-001 as OVER_CAP', "
                "'approve item EXP-002', 'request info for EXP-003 <details>', "
                "or set kind='finalise'."
            )
            return self._build_observation(
                scenario_data,
                messages=[self._state.last_action_error],
                done=False,
                reward=0.0,
                final_report=None,
                final_score=None,
            )

        if action_type in {"flag", "approve", "request_info"}:
            if item_id not in valid_ids:
                self._state.last_action_error = f"Invalid item id: {item_id}"
                return self._build_observation(
                    scenario_data,
                    messages=[self._state.last_action_error],
                    done=False,
                    reward=0.0,
                    final_report=None,
                    final_score=None,
                )

            if self._item_status.get(item_id, "unreviewed") != "unreviewed":
                self._state.last_action_error = f"Item already reviewed: {item_id}"
                return self._build_observation(
                    scenario_data,
                    messages=[self._state.last_action_error],
                    done=False,
                    reward=0.0,
                    final_report=None,
                    final_score=None,
                )

            if action_type == "flag" and reason_code not in scenario_data.allowed_reason_codes:
                self._state.last_action_error = (
                    f"Invalid reason code: {reason_code}. Must be one of allowed_reason_codes."
                )
                return self._build_observation(
                    scenario_data,
                    messages=[self._state.last_action_error],
                    done=False,
                    reward=0.0,
                    final_report=None,
                    final_score=None,
                )

        # From here onward, mutation is allowed.
        self._state.step_count += 1
        self._state.actions_taken += 1
        self._state.last_action_error = None

        progress_old = self._clamp01(self.progress_score)
        self.cumulative_score = progress_old
        messages: list[str] = []
        done = False
        reward = 0.0
        final_report: ExtendedFinalReport | None = None
        final_score: float | None = None
        step_reward_raw = 0
        budget_spend = 1

        if "suggest risky items" in message or ("suggest" in message and "risk" in message):
            self.risk_by_item = self._build_observation(scenario_data).risk_by_item
            top_items = sorted(self.risk_by_item.items(), key=lambda x: x[1], reverse=True)[:3]
            suggestions = [f"{iid} (risk: {round(score, 2)})" for iid, score in top_items]
            msg = "Suggested risky items: " + ", ".join(suggestions)
            if not hasattr(self, "messages"):
                self.messages = []
            self.messages.append(msg)
            progress_new = self._current_progress()
            self.progress_score = progress_new
            self.cumulative_score = progress_new
            return self._build_observation(
                scenario_data,
                messages=[msg],
                done=False,
                reward=0.0,
                final_report=None,
                final_score=None,
            )

        if action_type == "finalise":
            self._state.finalised = True
            done = True
            final_report = self._compute_final_report()
            final_score = round(self._clamp01(final_report.final_score), 4)
            self._final_report = final_report
            self._state.final_score = final_score
            progress_new = final_score
            self.progress_score = progress_new
            self.cumulative_score = progress_new
            step_reward_raw = 1 if final_report.report_decision_correct else -1
            reward = self._normalized_binary_reward(step_reward_raw)
            if not hasattr(self, "messages"):
                self.messages = []
            self.messages.append(f"Final Score: {round(final_score * 100, 2)}%")
            messages.append(f"Final Score: {round(final_score * 100, 2)}%")
            messages.append("Report finalised.")

        elif action_type == "auto_audit":
            unreviewed_items = [
                item
                for item in scenario_data.items
                if self._item_status.get(item.item_id, "unreviewed") == "unreviewed"
            ]
            ranked_items = sorted(
                unreviewed_items,
                key=lambda item: (
                    -self._auto_priority(item, scenario_data.company_policy),
                    item.item_id,
                ),
            )
            review_capacity = min(self.audit_budget_remaining, len(ranked_items))
            budget_spend = review_capacity

            reviewed_count = 0
            flagged_count = 0
            needs_info_count = 0
            approved_count = 0

            for item in ranked_items:
                item_id_local = item.item_id
                reasons_for_flag = self._collect_auto_rule_reasons(
                    item, scenario_data.company_policy
                )
                allowed_reasons_for_flag = [
                    r for r in reasons_for_flag if r in scenario_data.allowed_reason_codes
                ]
                if reviewed_count < review_capacity:
                    reviewed_count += 1

                has_high_confidence = (
                    "FORBIDDEN_MERCHANT" in allowed_reasons_for_flag
                    or "BLOCKED_MCC" in allowed_reasons_for_flag
                )
                has_medium_confidence = (
                    "OVER_CAP" in allowed_reasons_for_flag
                    or "MISSING_RECEIPT" in allowed_reasons_for_flag
                )

                if has_high_confidence:
                    self._item_status[item_id_local] = "flagged"
                    self.flagged_items.add(item_id_local)
                    primary_reason = allowed_reasons_for_flag[0]
                    self._state.flags.append(f"{item_id_local}:{primary_reason}")
                    flagged_count += 1
                    messages.append(
                        f"{item_id_local} flagged: {', '.join(allowed_reasons_for_flag)}"
                    )
                    if item_id_local in self.fraud_ground_truth:
                        self.correct_flagged_items.add(item_id_local)
                elif has_medium_confidence:
                    self._item_status[item_id_local] = "needs_info"
                    self._state.info_requests.append(f"{item_id_local}:AUTO_NEEDS_INFO")
                    needs_info_count += 1
                    messages.append(
                        f"{item_id_local} needs_info: {', '.join(allowed_reasons_for_flag)}"
                    )
                else:
                    self._item_status[item_id_local] = "approved"
                    self._state.approvals.append(item_id_local)
                    approved_count += 1
                    messages.append(f"{item_id_local} approved")

            self._state.finalised = True
            done = True
            final_report = self._compute_final_report()
            final_score = round(self._clamp01(final_report.final_score), 4)
            self._final_report = final_report
            self._state.final_score = final_score
            progress_new = final_score
            self.progress_score = progress_new
            self.cumulative_score = progress_new
            step_reward_raw = 1 if final_report.report_decision_correct else -1
            reward = self._normalized_binary_reward(step_reward_raw)

            messages.append(
                f"Auto audit completed: reviewed {reviewed_count}/{len(ranked_items)} items using available budget, "
                f"flagged {flagged_count}, needs_info {needs_info_count}, approved {approved_count}."
            )
            messages.append(f"Final Score: {round(final_score * 100, 2)}%")
            messages.append("Report finalised.")

        elif action_type == "flag":
            assert item_id is not None and reason_code is not None
            self._item_status[item_id] = "flagged"
            self._state.flags.append(f"{item_id}:{reason_code}")
            self.flagged_items.add(item_id)
            target_item = next(item for item in scenario_data.items if item.item_id == item_id)

            if reason_code == "DUPLICATE":
                is_correct = item_id in self.duplicate_items
            elif reason_code == "SPLIT_TRANSACTION":
                is_correct = item_id in self.split_transaction_items
            else:
                is_correct = self._is_flag_reason_correct(
                    target_item, reason_code, scenario_data.company_policy
                )

            reasons: list[str] = []
            if target_item.merchant_descriptor in scenario_data.company_policy.forbidden_merchants:
                reasons.append("forbidden merchant")
            receipt_required = (
                target_item.submitted_amount
                > scenario_data.company_policy.receipt_required_over_amount
            )
            if receipt_required and not target_item.receipt_present:
                reasons.append("missing receipt")
            category = target_item.submitted_category
            amount = target_item.submitted_amount
            if (
                category in scenario_data.company_policy.caps_by_category
                and amount > scenario_data.company_policy.caps_by_category[category]
            ):
                reasons.append("over category cap")
            if target_item.item_id in self.detect_split_transactions():
                reasons.append("split transaction pattern")
            if not reasons:
                reasons.append("suspicious pattern")

            messages.append(f"{target_item.item_id} flagged: " + ", ".join(reasons))
            messages.append(f"Flag recorded for {item_id}.")
            if is_correct:
                self.correct_flagged_items.add(item_id)
                step_reward_raw = 1
            else:
                step_reward_raw = -1

        elif action_type == "approve":
            assert item_id is not None
            self._item_status[item_id] = "approved"
            self._state.approvals.append(item_id)
            if item_id in self.fraud_ground_truth:
                messages.append(f"Approved {item_id} (fraudulent item).")
                step_reward_raw = -1
            else:
                messages.append(f"Approved {item_id}.")
                step_reward_raw = 1

        elif action_type == "request_info":
            assert item_id is not None
            self._item_status[item_id] = "needs_info"
            entry = item_id if not note else f"{item_id}:{note}"
            self._state.info_requests.append(entry)
            messages.append(
                f"Requested additional info for {item_id}."
                if not note
                else f"Requested additional info for {item_id}: {note}"
            )
            step_reward_raw = 0

        self.audit_budget_remaining = max(0, self.audit_budget_remaining - budget_spend)
        self._state.audit_budget_remaining = self.audit_budget_remaining
        if self.audit_budget_remaining == 0:
            messages.append("Audit budget exhausted")
        messages.append(f"Budget remaining: {self.audit_budget_remaining}")

        if not done:
            progress_new = self._current_progress()
            self.progress_score = progress_new
            self.cumulative_score = progress_new
            reward = self._normalized_binary_reward(step_reward_raw)
        reward = round(self._clamp01(reward), 4)

        return self._build_observation(
            scenario_data,
            messages=messages,
            done=done,
            reward=reward,
            final_report=final_report,
            final_score=final_score,
        )

    @property
    def state(self) -> AuditGuardState:
        return self._state
