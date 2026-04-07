import { useEffect, useMemo, useRef, useState } from "react";
import ProgressBar from "./ProgressBar";
import AIInsights from "./AIInsights";

const API_BASE = "http://127.0.0.1:8000";

const normalize = (value) => String(value || "").trim().toLowerCase();

const resolveItemId = (item, index) =>
  item?.id ?? item?.item_id ?? item?.expense_id ?? `item-${index}`;

const getRiskLabel = (score) => {
  const numeric = Number(score);
  if (!Number.isFinite(numeric)) return "LOW";
  if (numeric < 0.3) return "LOW";
  if (numeric < 0.8) return "MEDIUM";
  return "HIGH";
};

const getRiskBucket = (riskValue) => {
  if (typeof riskValue === "string") {
    const upper = riskValue.trim().toUpperCase();
    if (upper === "HIGH") return "HIGH";
    if (upper === "MEDIUM") return "MEDIUM";
    if (upper === "LOW") return "LOW";
    return getRiskLabel(riskValue);
  }

  return getRiskLabel(riskValue);
};

const getRiskPresentation = (riskValue) => {
  const bucket = getRiskBucket(riskValue);
  if (bucket === "HIGH") {
    return {
      label: "HIGH RISK",
      badgeClass: "bg-red-100 text-red-700",
      cardClass: "border-red-300 shadow-[0_0_15px_rgba(255,0,0,0.3)]",
    };
  }
  if (bucket === "MEDIUM") {
    return {
      label: "MEDIUM RISK",
      badgeClass: "bg-yellow-100 text-yellow-700",
      cardClass: "",
    };
  }
  return {
    label: "LOW RISK",
    badgeClass: "bg-green-100 text-green-700",
    cardClass: "",
  };
};

const getSeverityPresentation = (severityValue) => {
  const severity = String(severityValue || "").trim().toUpperCase();
  if (severity === "HIGH") {
    return {
      label: "HIGH",
      badgeClass: "bg-red-100 text-red-700 border-red-200",
      barClass: "bg-red-500",
    };
  }
  if (severity === "MEDIUM") {
    return {
      label: "MEDIUM",
      badgeClass: "bg-amber-100 text-amber-700 border-amber-200",
      barClass: "bg-amber-500",
    };
  }
  if (severity === "LOW") {
    return {
      label: "LOW",
      badgeClass: "bg-emerald-100 text-emerald-700 border-emerald-200",
      barClass: "bg-emerald-500",
    };
  }
  return {
    label: "UNKNOWN",
    badgeClass: "bg-slate-100 text-slate-700 border-slate-200",
    barClass: "bg-slate-400",
  };
};

const getSeverityPresentationFromScore = (riskScore) => {
  const severity = getRiskLabel(riskScore);
  return getSeverityPresentation(severity);
};

const clampRiskScore = (value) => {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return 0;
  return Math.max(0, Math.min(1, numeric));
};

const getSuggestedFlagReason = ({
  item,
  itemId,
  policy,
  riskByItem,
  allowedReasonCodes,
}) => {
  const safePolicy = policy || {};
  const safeRiskMap = riskByItem || {};
  const safeAllowedCodes = Array.isArray(allowedReasonCodes) ? allowedReasonCodes : [];

  const forbiddenMerchants = new Set(
    (safePolicy.forbidden_merchants || []).map((merchant) => normalize(merchant))
  );
  const capsByCategory = safePolicy.caps_by_category || {};

  const merchant = normalize(item?.merchant_descriptor || item?.merchant);
  const amount = Number(item?.submitted_amount ?? item?.amount ?? 0);
  const category = item?.submitted_category || item?.category;
  const categoryCap = Number(capsByCategory?.[category]);
  const riskBucket = getRiskLabel(safeRiskMap[itemId]);

  if (forbiddenMerchants.has(merchant)) {
    return {
      action: "FLAG",
      reasonCode: "FORBIDDEN_MERCHANT",
      displayText: "AI Suggestion: Flag (Forbidden Merchant)",
    };
  }

  if (Number.isFinite(categoryCap) && amount > categoryCap) {
    return {
      action: "FLAG",
      reasonCode: "OVER_CAP",
      displayText: "AI Suggestion: Flag (Over Cap)",
    };
  }

  if (riskBucket === "HIGH") {
    const highRiskReason = safeAllowedCodes.includes("HIGH_RISK")
      ? "HIGH_RISK"
      : safeAllowedCodes.includes("NEEDS_INFO")
        ? "NEEDS_INFO"
        : safeAllowedCodes[0] || "NEEDS_INFO";

    return {
      action: "FLAG",
      reasonCode: highRiskReason,
      displayText: "AI Suggestion: Flag (High Risk)",
    };
  }

  return {
    action: "APPROVE",
    reasonCode: "",
    displayText: "AI Suggestion: Approve",
  };
};

export default function Dashboard() {
  const [observation, setObservation] = useState(null);
  const [items, setItems] = useState([]);
  const [itemStatuses, setItemStatuses] = useState({});
  const [riskScores, setRiskScores] = useState({});
  const [reviewedCountState, setReviewedCountState] = useState(0);
  const [loading, setLoading] = useState(true);
  const [mounted, setMounted] = useState(false);
  const [showFlagModal, setShowFlagModal] = useState(false);
  const [selectedItem, setSelectedItem] = useState(null);
  const [selectedReason, setSelectedReason] = useState("");
  const [suggestedReason, setSuggestedReason] = useState("");

  const [actionLoading, setActionLoading] = useState(false);
  const [pendingItemId, setPendingItemId] = useState(null);
  const [pendingAction, setPendingAction] = useState("");
  const [expandedExplanations, setExpandedExplanations] = useState({});
  const hasReset = useRef(false);

  const syncFromObservation = (nextObservation) => {
    const safeObservation = { ...(nextObservation || {}) };
    const base = safeObservation?.state || safeObservation;
    console.log("STATUS MAP FROM BACKEND:", base);
    const nextItems = Array.isArray(base?.items) ? [...base.items] : [];
    const nextItemStatusMap = {
      ...(base?.item_statuses || base?.item_status || base?.item_status_map || {}),
    };
    const nextRiskScores = {
      ...(base?.risk_scores ||
        base?.risk_by_item ||
        base?.risk_map ||
        {}),
    };

    const reviewedCount =
      typeof base?.reviewed_count === "number"
        ? base.reviewed_count
        : Object.values(nextItemStatusMap).filter((status) => status !== "unreviewed")
            .length;

    console.log("UPDATED STATUS MAP:", nextItemStatusMap);
    setObservation(JSON.parse(JSON.stringify(base)));
    setItems(JSON.parse(JSON.stringify(nextItems)));
    setItemStatuses(JSON.parse(JSON.stringify(nextItemStatusMap)));
    setRiskScores(JSON.parse(JSON.stringify(nextRiskScores)));
    setReviewedCountState(() => reviewedCount);
  };

  const resetEnvironment = async () => {
    setMounted(true);
    console.log("RESET CALLED");
    console.count("RESET TRIGGERED");

    try {
      const res = await fetch(`${API_BASE}/reset`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ seed: 42 }),
      });
      const response = await res.json();
      console.log("RESET RESPONSE", response);
      syncFromObservation(response?.observation);
      setExpandedExplanations({});
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!hasReset.current) {
      resetEnvironment();
      hasReset.current = true;
    }
  }, []);

  useEffect(() => {
    console.log(itemStatuses);
  }, [itemStatuses]);

  const handleAction = async (actionMessage) => {
    const remaining = Number(observation?.audit_budget_remaining);
    if (Number.isFinite(remaining) && remaining === 0) {
      console.warn("Audit budget exhausted. Blocking step action.");
      return { observation };
    }

    console.log("Sending action:", actionMessage);
    const res = await fetch(`${API_BASE}/step`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({
        action: { message: actionMessage },
      }),
    });

    const data = await res.json();
    console.log("BACKEND RESPONSE:", data);
    console.log("FULL RESPONSE:", data);
    syncFromObservation(data?.observation);
    return data;
  };

  const allowedReasonCodes = observation?.allowed_reason_codes || [];

  const policyRows = useMemo(() => {
    if (!observation?.company_policy) return [];
    const policy = observation.company_policy;
    return [
      {
        label: "Receipt Threshold",
        value:
          typeof policy.receipt_required_over_amount === "number"
            ? `$${policy.receipt_required_over_amount.toFixed(2)}`
            : "-",
      },
      {
        label: "Forbidden Merchants",
        value: policy.forbidden_merchants?.length
          ? policy.forbidden_merchants.join(", ")
          : "None",
      },
      {
        label: "Blocked MCCs",
        value: policy.blocked_mccs?.length ? policy.blocked_mccs.join(", ") : "None",
      },
      {
        label: "Split Window (hrs)",
        value: policy.split_transaction_window_hours ?? "-",
      },
    ];
  }, [observation]);

  const getItemId = (item, index) => resolveItemId(item, index);

  const getStatus = (item, index) => {
    const itemId = getItemId(item, index);
    return itemStatuses?.[itemId] ?? item?.status ?? "unreviewed";
  };

  const toggleExplanation = (itemId) => {
    setExpandedExplanations((prev) => ({
      ...prev,
      [itemId]: !prev[itemId],
    }));
  };

  const handleApprove = async (itemId) => {
    const remaining = Number(observation?.audit_budget_remaining);
    if (Number.isFinite(remaining) && remaining === 0) return;
    if (actionLoading) return;
    if ((itemStatuses?.[itemId] ?? "unreviewed") !== "unreviewed") return;

    setActionLoading(true);
    setPendingItemId(itemId);
    setPendingAction("approve");

    try {
      await handleAction(`approve item ${itemId}`);
    } catch (error) {
      console.error(error);
    } finally {
      setActionLoading(false);
      setPendingItemId(null);
      setPendingAction("");
    }
  };

  const deriveDefaultReason = (item) => {
    const suggested = getSuggestedFlagReason({
      item,
      itemId: resolveItemId(item),
      policy: observation?.company_policy,
      riskByItem: riskScores,
      allowedReasonCodes,
    });

    const derived = suggested.reasonCode;
    if (!derived) {
      if (allowedReasonCodes.includes("NEEDS_INFO")) return "NEEDS_INFO";
      return allowedReasonCodes[0] || "NEEDS_INFO";
    }
    if (allowedReasonCodes.length === 0) return derived;
    if (allowedReasonCodes.includes(derived)) return derived;
    if (allowedReasonCodes.includes("NEEDS_INFO")) return "NEEDS_INFO";
    return allowedReasonCodes[0];
  };

  const handleFlagClick = (item, index) => {
    const remaining = Number(observation?.audit_budget_remaining);
    if (Number.isFinite(remaining) && remaining === 0) return;
    const itemId = resolveItemId(item, index);
    if ((itemStatuses?.[itemId] ?? "unreviewed") !== "unreviewed") return;
    console.log(item);
    console.log("Selected item:", item);
    const normalizedItem = {
      ...item,
      id: itemId,
    };

    const defaultReason = deriveDefaultReason(normalizedItem);
    setSuggestedReason(defaultReason);
    setSelectedReason(defaultReason);
    setSelectedItem({
      ...normalizedItem,
    });
    setShowFlagModal(true);
  };

  const handleFlagConfirm = async () => {
    const remaining = Number(observation?.audit_budget_remaining);
    if (Number.isFinite(remaining) && remaining === 0) return;
    if (
      !selectedItem ||
      (!selectedItem.id && !selectedItem.item_id && !selectedItem.expense_id)
    ) {
      console.error("Invalid selected item", selectedItem);
      return;
    }
    const itemId =
      selectedItem.id ?? selectedItem.item_id ?? selectedItem.expense_id;
    if ((itemStatuses?.[itemId] ?? "unreviewed") !== "unreviewed") return;

    const reasonToSend =
      selectedReason ||
      (allowedReasonCodes.includes("NEEDS_INFO")
        ? "NEEDS_INFO"
        : allowedReasonCodes[0] || "NEEDS_INFO");

    console.log("Flag confirmed", itemId);
    console.log("Sending ID:", itemId);
    console.log("Sending reason:", reasonToSend);
    console.log("Flagging item:", itemId);

    if (actionLoading) return;
    setActionLoading(true);
    setPendingItemId(itemId);
    setPendingAction("flag");

    try {
      await handleAction(`flag item ${itemId} as ${reasonToSend}`);
    } catch (error) {
      console.error("Backend error:", error);
    } finally {
      setShowFlagModal(false);
      setSelectedItem(null);
      setSelectedReason("");
      setSuggestedReason("");
      setActionLoading(false);
      setPendingItemId(null);
      setPendingAction("");
    }
  };

  const getStatusBadgeClass = (status) => {
    if (status === "approved") return "bg-emerald-100 text-emerald-700 border-emerald-200";
    if (status === "flagged") return "bg-rose-100 text-rose-700 border-rose-200";
    if (status === "needs_info") return "bg-amber-100 text-amber-700 border-amber-200";
    return "bg-white/50 text-gray-700 border-white/60";
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-[#ff9a9e] via-[#a18cd1] to-[#89f7fe] p-10">
        <div className="mx-auto flex min-h-[calc(100vh-5rem)] max-w-6xl items-center justify-center">
          <div className="animate-pulse rounded-2xl border border-white/50 bg-white/40 p-6 shadow-lg backdrop-blur-xl">
            <p className="text-lg font-medium text-gray-900">Loading...</p>
            <p className="mt-1 text-sm text-gray-700">Preparing your audit scenario</p>
          </div>
        </div>
      </div>
    );
  }

  if (!observation) return null;

  const messages = observation.messages || [];
  const totalItems = items.length;
  const budgetRemainingRaw = observation?.audit_budget_remaining;
  const budgetRemaining = Number(budgetRemainingRaw);
  const hasBudgetValue =
    budgetRemainingRaw !== undefined &&
    budgetRemainingRaw !== null &&
    Number.isFinite(budgetRemaining);
  const budgetExhausted = hasBudgetValue && budgetRemaining === 0;
  const budgetTotalRaw =
    observation?.audit_budget_total ??
    observation?.audit_budget ??
    (hasBudgetValue ? budgetRemaining : null);
  const budgetTotal = Number(budgetTotalRaw);
  const budgetTotalDisplay = Number.isFinite(budgetTotal)
    ? budgetTotal
    : hasBudgetValue
      ? budgetRemaining
      : "-";

  const reviewedCount =
    typeof reviewedCountState === "number"
      ? reviewedCountState
      : Object.values(itemStatuses).filter((status) => status !== "unreviewed").length;
  const aiExplanations = observation?.ai_explanations || {};

  return (
    <div className="min-h-screen bg-gradient-to-br from-[#ff9a9e] via-[#a18cd1] to-[#89f7fe] p-10">
      <div className="mx-auto flex min-h-[calc(100vh-5rem)] max-w-6xl items-center justify-center">
        <div
          className={`relative w-full overflow-hidden rounded-3xl border border-white/20 bg-white/10 p-10 shadow-[0_10px_40px_rgba(0,0,0,0.25)] backdrop-blur-3xl transition-all duration-700 before:absolute before:inset-0 before:rounded-3xl before:bg-white/10 before:opacity-50 before:blur-xl ${
            mounted ? "translate-y-0 opacity-100" : "translate-y-3 opacity-0"
          } hover:scale-[1.005]`}
        >
          <div className="relative z-10 flex flex-col gap-8">
            <div className="flex flex-col gap-2">
              <p className="text-sm font-medium uppercase tracking-[0.2em] text-gray-600">
                Fraud Intelligence Workspace
              </p>
              <h1 className="text-3xl font-bold text-gray-900 md:text-4xl">AuditGuard Dashboard</h1>
              <p className="max-w-3xl text-sm text-gray-700 md:text-base">
                {observation.task_brief || "Inspect expense behavior and surface risky reimbursement patterns."}
              </p>
            </div>

            <div className="space-y-8">
              <div className="mb-6 rounded-xl bg-white/40 p-4 shadow">
                <h2 className="text-lg font-semibold text-gray-800">
                  Final Score: {Number(observation.final_score ?? 0).toFixed(4)}
                </h2>
                <p className="text-gray-600">
                  Reviewed: {reviewedCount} / {totalItems}
                </p>
                <p className="text-gray-600">
                  Actions Remaining: {hasBudgetValue ? budgetRemaining : "-"} /{" "}
                  {budgetTotalDisplay}
                </p>
                {reviewedCount >= totalItems && totalItems > 0 && (
                  <p className="mt-2 text-sm font-semibold text-gray-800">Audit Completed!</p>
                )}
                {budgetExhausted && (
                  <p className="mt-2 text-sm font-semibold text-rose-700">
                    Audit budget exhausted. Please reset to continue.
                  </p>
                )}
              </div>

              <section className="rounded-xl border border-white/50 bg-white/40 p-4 shadow-lg backdrop-blur-xl">
                <h2 className="mb-2 text-lg font-semibold text-gray-900">System Logs</h2>
                <div className="space-y-1">
                  {messages.length > 0 ? (
                    messages.map((msg, index) => (
                      <p key={`${msg}-${index}`} className="text-sm text-gray-700">
                        - {msg}
                      </p>
                    ))
                  ) : (
                    <p className="text-sm text-gray-700">No messages yet.</p>
                  )}
                </div>
              </section>

              <AIInsights
                items={items}
                itemStatuses={itemStatuses}
                policy={observation.company_policy}
                riskByItem={riskScores}
              />

              <section>
                <h2 className="mb-4 text-lg font-semibold text-gray-900">Policy Snapshot</h2>
                <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                  {policyRows.map((row) => (
                    <div
                      key={row.label}
                      className="rounded-2xl border border-white/50 bg-white/40 p-4 shadow-lg backdrop-blur-xl"
                    >
                      <p className="text-xs uppercase tracking-wider text-gray-600">{row.label}</p>
                      <p className="mt-1 text-sm font-medium text-gray-900">{String(row.value)}</p>
                    </div>
                  ))}
                </div>
              </section>

              <section>
                <div className="mb-4 flex items-center justify-between">
                  <h2 className="text-lg font-semibold text-gray-900">Expense Items</h2>
                  <p className="text-sm text-gray-700">{items.length} items</p>
                </div>
                <ProgressBar itemStatuses={itemStatuses} />

                <div className="grid grid-cols-1 gap-5 md:grid-cols-2 xl:grid-cols-3">
                  {items?.map((item, index) => {
                    const itemId = getItemId(item, index);
                    const status = getStatus(item, index);
                    const merchant = item.merchant_descriptor || item.merchant || "Unknown Merchant";
                    const amount = item.submitted_amount ?? item.amount ?? 0;
                    const riskValue = riskScores?.[itemId] ?? observation?.risk_by_item?.[itemId];
                    const syncedRiskValue = riskScores?.[itemId];
                    const risk = getRiskPresentation(riskValue);
                    const suggestion = getSuggestedFlagReason({
                      item,
                      itemId,
                      policy: observation?.company_policy,
                      riskByItem: riskScores,
                      allowedReasonCodes,
                    });
                    const isPendingRow = actionLoading && pendingItemId === itemId;
                    const explanation = aiExplanations?.[itemId];
                    const hasAiExplanation = Boolean(explanation);
                    const explanationRiskScore = hasAiExplanation
                      ? clampRiskScore(explanation?.risk_score)
                      : clampRiskScore(syncedRiskValue ?? riskValue);
                    const severity = hasAiExplanation
                      ? getSeverityPresentationFromScore(explanationRiskScore)
                      : getSeverityPresentation("UNKNOWN");
                    const explanationDecisionHint = hasAiExplanation
                      ? explanation?.decision_hint || "N/A"
                      : "N/A";
                    const explanationReasons = Array.isArray(explanation?.reasons)
                      ? explanation.reasons
                      : [];
                    const explanationExpanded = Boolean(expandedExplanations[itemId]);

                    return (
                      <div
                        key={itemId}
                        className={`rounded-2xl border border-white/50 bg-white/40 p-5 shadow-lg backdrop-blur-xl transition duration-300 hover:scale-[1.02] ${risk.cardClass}`}
                      >
                        <div className="mb-4 flex items-start justify-between gap-3">
                          <div>
                            <p className="text-lg font-semibold leading-tight text-gray-900">{merchant}</p>
                            <p className="mt-1 text-sm text-gray-700">Item: {itemId}</p>
                          </div>
                          <div className="flex flex-col items-end gap-2">
                            <span className={`rounded-full px-3 py-1 text-sm font-semibold ${risk.badgeClass}`}>
                              {risk.label}
                            </span>
                            <span
                              className={`rounded-full border px-3 py-1 text-xs font-medium capitalize ${getStatusBadgeClass(
                                status
                              )}`}
                            >
                              {status}
                            </span>
                          </div>
                        </div>

                        <p className="text-2xl font-semibold text-gray-900">INR {Number(amount).toFixed(2)}</p>
                        <p className="mt-1 text-sm text-gray-700">
                          Category: {item.submitted_category || item.category || "N/A"}
                        </p>
                        <p className="mt-1 text-xs font-medium text-indigo-700">{suggestion.displayText}</p>

                        <div className="mt-5 flex gap-3">
                          <button
                            type="button"
                            onClick={() => handleApprove(itemId)}
                            disabled={
                              actionLoading ||
                              status !== "unreviewed" ||
                              observation.done ||
                              budgetExhausted
                            }
                            className="flex-1 rounded-xl bg-emerald-500 px-4 py-2 text-sm font-semibold text-white shadow-md transition hover:bg-emerald-600 disabled:cursor-not-allowed disabled:bg-emerald-300"
                          >
                            {isPendingRow && pendingAction === "approve" ? "Sending..." : "Approve"}
                          </button>
                          <button
                            type="button"
                            onClick={() => handleFlagClick(item, index)}
                            disabled={
                              actionLoading ||
                              status !== "unreviewed" ||
                              observation.done ||
                              budgetExhausted
                            }
                            className="flex-1 rounded-xl bg-rose-500 px-4 py-2 text-sm font-semibold text-white shadow-md transition hover:bg-rose-600 disabled:cursor-not-allowed disabled:bg-rose-300"
                          >
                            {isPendingRow && pendingAction === "flag" ? "Sending..." : "Flag"}
                          </button>
                        </div>

                        <div className="mt-4">
                          <button
                            type="button"
                            onClick={() => toggleExplanation(itemId)}
                            className="rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-1.5 text-xs font-semibold text-indigo-700 transition hover:bg-indigo-100"
                          >
                            {explanationExpanded ? "Hide AI Explanation" : "Show AI Explanation"}
                          </button>
                        </div>

                        {explanationExpanded && (
                          <div className="mt-4 rounded-xl border border-white/70 bg-white/70 p-4 shadow-sm">
                            <div className="flex flex-wrap items-center gap-2">
                              <span
                                className={`rounded-full border px-2.5 py-1 text-xs font-semibold ${severity.badgeClass}`}
                              >
                                {severity.label}
                              </span>
                              <span className="rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 text-xs font-semibold text-slate-700">
                                Risk Score: {explanationRiskScore.toFixed(2)}
                              </span>
                              <span className="rounded-full border border-indigo-200 bg-indigo-50 px-2.5 py-1 text-xs font-semibold text-indigo-700">
                                Hint: {String(explanationDecisionHint).toUpperCase()}
                              </span>
                            </div>

                            <div className="mt-3">
                              <div className="h-2 w-full overflow-hidden rounded-full bg-slate-200">
                                <div
                                  className={`h-full rounded-full transition-all duration-300 ${severity.barClass}`}
                                  style={{ width: `${Math.round(explanationRiskScore * 100)}%` }}
                                />
                              </div>
                            </div>

                            <div className="mt-3">
                              {hasAiExplanation && explanationReasons.length > 0 ? (
                                <ul className="list-disc space-y-1 pl-5 text-sm text-gray-700">
                                  {explanationReasons.map((reason, reasonIndex) => (
                                    <li key={`${itemId}-reason-${reasonIndex}`}>{reason}</li>
                                  ))}
                                </ul>
                              ) : (
                                <p className="text-sm text-gray-700">No AI explanation available yet.</p>
                              )}
                            </div>
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </section>

              {observation.final_report && (
                <section className="rounded-xl bg-white/40 p-4 shadow">
                  <h2 className="text-lg font-semibold text-gray-800">Final Report</h2>
                  <p className="mt-1 text-sm text-gray-700">
                    {observation.final_report.summary || "Final report received."}
                  </p>
                </section>
              )}
            </div>
          </div>
        </div>
      </div>
      {showFlagModal && (
        <FlagModal
          item={selectedItem}
          onClose={() => {
            setShowFlagModal(false);
            setSelectedItem(null);
            setSelectedReason("");
            setSuggestedReason("");
          }}
          onConfirm={handleFlagConfirm}
          loading={actionLoading}
          selectedReason={selectedReason}
          onReasonChange={setSelectedReason}
          allowedReasonCodes={allowedReasonCodes}
          suggestedReason={suggestedReason}
        />
      )}
    </div>
  );
}

function FlagModal({
  item,
  onClose,
  onConfirm,
  loading,
  selectedReason,
  onReasonChange,
  allowedReasonCodes,
  suggestedReason,
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-xl">
        <h3 className="text-lg font-semibold text-gray-900">Confirm Flag</h3>
        <p className="mt-2 text-sm text-gray-700">
          Flag item <span className="font-semibold">{item?.id ?? item?.item_id}</span>?
        </p>
        <div className="mt-4">
          <label htmlFor="reason-code" className="mb-1 block text-sm font-medium text-gray-700">
            Reason Code
          </label>
          <select
            id="reason-code"
            value={selectedReason}
            onChange={(e) => onReasonChange(e.target.value)}
            disabled={loading || allowedReasonCodes.length === 0}
            className={`w-full rounded-lg border px-3 py-2 text-sm text-gray-800 focus:outline-none focus:ring-2 disabled:cursor-not-allowed disabled:bg-gray-100 ${
              selectedReason === suggestedReason
                ? "border-emerald-400 focus:border-emerald-500 focus:ring-emerald-300"
                : "border-gray-300 focus:border-indigo-500 focus:ring-indigo-300"
            }`}
          >
            {allowedReasonCodes.length > 0 ? (
              allowedReasonCodes.map((reason) => (
                <option key={reason} value={reason}>
                  {reason}
                  {reason === suggestedReason ? " (Recommended)" : ""}
                </option>
              ))
            ) : (
              <option value="NEEDS_INFO">NEEDS_INFO</option>
            )}
          </select>
        </div>
        <div className="mt-5 flex justify-end gap-3">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => {
              onConfirm();
            }}
            disabled={loading}
            className="rounded-lg bg-rose-500 px-4 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:bg-rose-300"
          >
            {loading ? "Sending..." : "Confirm Flag"}
          </button>
        </div>
      </div>
    </div>
  );
}

