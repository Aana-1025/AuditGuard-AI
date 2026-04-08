import { useEffect, useMemo, useRef, useState } from "react";
import ProgressBar from "./ProgressBar";
import AIInsights from "./AIInsights";

const API_BASE = "http://127.0.0.1:8000";

const resolveObservation = (payload) => payload?.observation ?? payload ?? null;

const resolveItemId = (item, index) =>
  item?.item_id ?? item?.id ?? item?.expense_id ?? `item-${index}`;

const normalizeActionType = (value) => String(value || "").trim().toLowerCase();

const normalize = (value) => String(value || "").trim().toLowerCase();

const getRiskLabel = (score) => {
  const numeric = Number(score);
  if (!Number.isFinite(numeric)) return "LOW";
  if (numeric > 0.8) return "HIGH";
  if (numeric > 0.3) return "MEDIUM";
  return "LOW";
};

const getRiskPresentation = (riskValue) => {
  const label = getRiskLabel(riskValue);
  if (label === "HIGH") {
    return {
      label: "HIGH RISK",
      badgeClass: "bg-red-100 text-red-700",
      cardClass: "border-red-300 shadow-[0_0_15px_rgba(255,0,0,0.18)]",
    };
  }
  if (label === "MEDIUM") {
    return {
      label: "MEDIUM RISK",
      badgeClass: "bg-yellow-100 text-yellow-700",
      cardClass: "border-yellow-200",
    };
  }
  return {
    label: "LOW RISK",
    badgeClass: "bg-green-100 text-green-700",
    cardClass: "border-white/50",
  };
};

const getStatusBadgeClass = (status) => {
  if (status === "flagged") return "bg-rose-100 text-rose-700 border-rose-200";
  if (status === "approved") return "bg-emerald-100 text-emerald-700 border-emerald-200";
  return "bg-slate-100 text-slate-600 border-slate-200";
};

const getSuggestedFlagReason = ({ item, itemId, policy, riskByItem, allowedReasonCodes }) => {
  const safePolicy = policy || {};
  const safeRiskMap = riskByItem || {};
  const safeAllowedCodes = Array.isArray(allowedReasonCodes) ? allowedReasonCodes : [];
  const forbiddenMerchants = new Set(
    (safePolicy.forbidden_merchants || []).map((merchant) => normalize(merchant))
  );
  const merchant = normalize(item?.merchant_descriptor || item?.merchant);
  const amount = Number(item?.submitted_amount ?? item?.amount ?? 0);
  const category = item?.submitted_category || item?.category;
  const categoryCap = Number(safePolicy?.caps_by_category?.[category]);
  const risk = Number(safeRiskMap?.[itemId]);

  if (forbiddenMerchants.has(merchant) && safeAllowedCodes.includes("FORBIDDEN_MERCHANT")) {
    return {
      reasonCode: "FORBIDDEN_MERCHANT",
      displayText: "AI Suggestion: Flag (Forbidden Merchant)",
    };
  }

  if (Number.isFinite(categoryCap) && amount > categoryCap && safeAllowedCodes.includes("OVER_CAP")) {
    return {
      reasonCode: "OVER_CAP",
      displayText: "AI Suggestion: Flag (Over Cap)",
    };
  }

  if (risk > 0.8 && safeAllowedCodes.includes("NEEDS_INFO")) {
    return {
      reasonCode: "NEEDS_INFO",
      displayText: "AI Suggestion: Flag (Needs Review)",
    };
  }

  return {
    reasonCode: safeAllowedCodes[0] || "FORBIDDEN_MERCHANT",
    displayText: "AI Suggestion: Review Transaction",
  };
};

export default function Dashboard() {
  const [observation, setObservation] = useState(null);
  const [items, setItems] = useState([]);
  const [itemStatus, setItemStatus] = useState({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [mounted, setMounted] = useState(false);
  const [showFlagModal, setShowFlagModal] = useState(false);
  const [selectedItem, setSelectedItem] = useState(null);
  const [selectedReason, setSelectedReason] = useState("");
  const [expandedExplanations, setExpandedExplanations] = useState({});
  const hasReset = useRef(false);

  const syncFromBackend = (payload) => {
    const nextObservation = resolveObservation(payload);
    if (!nextObservation) {
      throw new Error("Missing observation in backend response");
    }

    setObservation(nextObservation);
    setItems(Array.isArray(nextObservation.items) ? nextObservation.items : []);
    setItemStatus(nextObservation.item_status || {});
  };

  const handleStep = async (body) => {
    if (loading) return;

    const actionType = normalizeActionType(body?.action_type);
    const itemId =
      body?.item_id == null || body?.item_id === ""
        ? undefined
        : String(body.item_id).trim().toUpperCase();
    const requestBody = {
      ...body,
      action_type: actionType,
      ...(itemId ? { item_id: itemId } : {}),
    };

    console.log("Audit action:", {
      action_type: requestBody.action_type,
      item_id: requestBody.item_id ?? null,
    });

    setLoading(true);
    setError("");

    try {
      const response = await fetch(`${API_BASE}/step`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(requestBody),
      });

      if (!response.ok) {
        throw new Error(`Step failed with status ${response.status}`);
      }

      const data = await response.json();
      syncFromBackend(data);
    } catch (err) {
      console.error("Step error:", err);
      setError("Unable to complete action. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  const handleReset = async () => {
    if (loading) return;

    setLoading(true);
    setError("");
    setShowFlagModal(false);
    setSelectedItem(null);
    setSelectedReason("");
    setExpandedExplanations({});

    try {
      const response = await fetch(`${API_BASE}/reset`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scenario: "easy", seed: 0 }),
      });

      if (!response.ok) {
        throw new Error(`Reset failed with status ${response.status}`);
      }

      const data = await response.json();
      syncFromBackend(data);
    } catch (err) {
      console.error("Reset error:", err);
      setError("Unable to load audit scenario.");
      setObservation(null);
      setItems([]);
      setItemStatus({});
    } finally {
      setLoading(false);
      setMounted(true);
    }
  };

  useEffect(() => {
    if (!hasReset.current) {
      hasReset.current = true;
      handleReset();
    }
  }, []);

  const allowedReasonCodes = observation?.allowed_reason_codes || [];
  const riskByItem = observation?.risk_by_item || {};
  const done = Boolean(observation?.done);
  const finalScore = observation?.final_score;

  const reviewedCount = useMemo(
    () => Object.values(itemStatus).filter((status) => status !== "unreviewed").length,
    [itemStatus]
  );

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

  const handleApprove = (itemId) => {
    const resolvedItemId = String(itemId || "").trim().toUpperCase();
    if (!resolvedItemId || loading || done || itemStatus[resolvedItemId] !== "unreviewed") {
      return;
    }
    handleStep({ action_type: "approve", item_id: resolvedItemId });
  };

  const handleFlagClick = (item, index) => {
    const itemId = resolveItemId(item, index);
    if (loading || done || itemStatus[itemId] !== "unreviewed") return;

    const suggestion = getSuggestedFlagReason({
      item,
      itemId,
      policy: observation?.company_policy,
      riskByItem,
      allowedReasonCodes,
    });

    setSelectedItem({ ...item, id: itemId });
    setSelectedReason(suggestion.reasonCode);
    setShowFlagModal(true);
  };

  const handleFlagConfirm = () => {
    if (!selectedItem || loading || done) return;
    const itemId = String(selectedItem.id ?? selectedItem.item_id ?? "").trim().toUpperCase();
    if (!itemId || itemStatus[itemId] !== "unreviewed") return;

    const reasonCode = selectedReason || allowedReasonCodes[0];
    setShowFlagModal(false);
    setSelectedItem(null);
    setSelectedReason("");
    handleStep({
      action_type: "flag",
      item_id: itemId,
      reason_code: reasonCode,
    });
  };

  const handleFinalise = () => {
    if (loading || done) return;
    handleStep({ action_type: "finalise" });
  };

  const toggleExplanation = (itemId) => {
    setExpandedExplanations((prev) => ({
      ...prev,
      [itemId]: !prev[itemId],
    }));
  };

  if (loading && !observation) {
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

  if (!observation) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-[#ff9a9e] via-[#a18cd1] to-[#89f7fe] p-10">
        <div className="mx-auto flex min-h-[calc(100vh-5rem)] max-w-6xl items-center justify-center">
          <div className="rounded-2xl border border-white/50 bg-white/40 p-6 shadow-lg backdrop-blur-xl">
            <p className="text-lg font-medium text-gray-900">Unable to load dashboard.</p>
            <p className="mt-1 text-sm text-gray-700">{error || "Please try resetting the scenario."}</p>
            <button
              type="button"
              onClick={handleReset}
              className="mt-4 rounded-xl bg-indigo-600 px-4 py-2 text-sm font-semibold text-white"
            >
              Retry
            </button>
          </div>
        </div>
      </div>
    );
  }

  const messages = observation.messages || [];
  const budgetRemaining = Number(observation.audit_budget_remaining ?? 0);
  const budgetTotal = Number(observation.audit_budget_total ?? 0);
  const budgetExhausted = Number.isFinite(budgetRemaining) && budgetRemaining <= 0;
  const canFinalise = !loading && !done;

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

            {error ? (
              <section className="rounded-xl border border-rose-200 bg-rose-50 p-4 shadow">
                <p className="text-sm font-medium text-rose-700">{error}</p>
              </section>
            ) : null}

            <div className="space-y-8">
              <div className="mb-6 rounded-xl bg-white/40 p-4 shadow">
                <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
                  <div>
                    <h2 className="text-lg font-semibold text-gray-800">
                      Final Score: {finalScore == null ? "--" : Number(finalScore).toFixed(4)}
                    </h2>
                    <p className="text-gray-600">
                      Reviewed: {reviewedCount} / {items.length}
                    </p>
                    <p className="text-gray-600">
                      Actions Remaining: {budgetRemaining} / {budgetTotal}
                    </p>
                  </div>
                  <div className="flex gap-3">
                    <button
                      type="button"
                      onClick={handleReset}
                      disabled={loading}
                      className="rounded-xl border border-gray-300 bg-white px-4 py-2 text-sm font-semibold text-gray-800 shadow-sm transition hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      {loading ? "Working..." : "Reset"}
                    </button>
                    <button
                      type="button"
                      onClick={handleFinalise}
                      disabled={!canFinalise}
                      className="rounded-xl bg-indigo-600 px-4 py-2 text-sm font-semibold text-white shadow-md transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:bg-indigo-300"
                    >
                      {loading ? "Working..." : "Finalise Audit"}
                    </button>
                  </div>
                </div>
                {done ? (
                  <p className="mt-3 text-sm font-semibold text-emerald-700">
                    Audit completed successfully. Final score recorded.
                  </p>
                ) : null}
                {budgetExhausted && !done ? (
                  <p className="mt-2 text-sm font-semibold text-rose-700">
                    Audit budget exhausted. Finalise or reset to continue.
                  </p>
                ) : null}
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
                itemStatuses={itemStatus}
                policy={observation.company_policy}
                riskByItem={riskByItem}
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
                <ProgressBar itemStatuses={itemStatus} />

                <div className="grid grid-cols-1 gap-5 md:grid-cols-2 xl:grid-cols-3">
                  {items.map((item, index) => {
                    const itemId = resolveItemId(item, index);
                    const status = itemStatus[itemId] ?? "unreviewed";
                    const merchant = item.merchant_descriptor || item.merchant || "Unknown Merchant";
                    const amount = item.submitted_amount ?? item.amount ?? 0;
                    const riskValue = riskByItem[itemId];
                    const risk = getRiskPresentation(riskValue);
                    const suggestion = getSuggestedFlagReason({
                      item,
                      itemId,
                      policy: observation.company_policy,
                      riskByItem,
                      allowedReasonCodes,
                    });
                    const actionLocked = loading || done || status !== "unreviewed";
                    const explanationExpanded = Boolean(expandedExplanations[itemId]);
                    const highRisk = Number(riskValue) > 0.8;

                    return (
                      <div
                        key={itemId}
                        className={`rounded-2xl border bg-white/40 p-5 shadow-lg backdrop-blur-xl transition duration-300 hover:scale-[1.02] ${
                          highRisk ? "border-red-300 shadow-[0_0_15px_rgba(255,0,0,0.18)]" : risk.cardClass
                        }`}
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
                            disabled={actionLocked}
                            className="flex-1 rounded-xl bg-emerald-500 px-4 py-2 text-sm font-semibold text-white shadow-md transition hover:bg-emerald-600 disabled:cursor-not-allowed disabled:bg-emerald-300"
                          >
                            {loading ? "Working..." : "Approve"}
                          </button>
                          <button
                            type="button"
                            onClick={() => handleFlagClick(item, index)}
                            disabled={actionLocked}
                            className="flex-1 rounded-xl bg-rose-500 px-4 py-2 text-sm font-semibold text-white shadow-md transition hover:bg-rose-600 disabled:cursor-not-allowed disabled:bg-rose-300"
                          >
                            {loading ? "Working..." : "Flag"}
                          </button>
                        </div>

                        <div className="mt-4">
                          <button
                            type="button"
                            onClick={() => toggleExplanation(itemId)}
                            className="rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-1.5 text-xs font-semibold text-indigo-700 transition hover:bg-indigo-100"
                          >
                            {explanationExpanded ? "Hide Details" : "Show Details"}
                          </button>
                        </div>

                        {explanationExpanded ? (
                          <div className="mt-4 rounded-xl border border-white/70 bg-white/70 p-4 shadow-sm">
                            <p className="text-sm text-gray-700">
                              Risk Score: {Number(riskValue ?? 0).toFixed(2)}
                            </p>
                            <p className="mt-1 text-sm text-gray-700">
                              Current Status: <span className="font-semibold capitalize">{status}</span>
                            </p>
                            <p className="mt-2 text-sm text-gray-700">
                              Receipt Present: {item.receipt_present ? "Yes" : "No"}
                            </p>
                          </div>
                        ) : null}
                      </div>
                    );
                  })}
                </div>
              </section>

              {done ? (
                <section className="rounded-xl border border-emerald-200 bg-emerald-50 p-4 shadow">
                  <h2 className="text-lg font-semibold text-emerald-800">Audit Result</h2>
                  <p className="mt-1 text-sm text-emerald-700">
                    Final Score: {finalScore == null ? "--" : Number(finalScore).toFixed(4)}
                  </p>
                  <p className="mt-1 text-sm text-emerald-700">Audit finalised successfully.</p>
                </section>
              ) : null}

              {observation.final_report ? (
                <section className="rounded-xl bg-white/40 p-4 shadow">
                  <h2 className="text-lg font-semibold text-gray-800">Final Report</h2>
                  <p className="mt-1 text-sm text-gray-700">
                    {observation.final_report.summary || "Final report received."}
                  </p>
                </section>
              ) : null}
            </div>
          </div>
        </div>
      </div>

      {showFlagModal ? (
        <FlagModal
          item={selectedItem}
          onClose={() => {
            if (loading) return;
            setShowFlagModal(false);
            setSelectedItem(null);
            setSelectedReason("");
          }}
          onConfirm={handleFlagConfirm}
          loading={loading}
          selectedReason={selectedReason}
          onReasonChange={setSelectedReason}
          allowedReasonCodes={allowedReasonCodes}
        />
      ) : null}
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
            onChange={(event) => onReasonChange(event.target.value)}
            disabled={loading || allowedReasonCodes.length === 0}
            className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-800 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-300 disabled:cursor-not-allowed disabled:bg-gray-100"
          >
            {allowedReasonCodes.length > 0 ? (
              allowedReasonCodes.map((reason) => (
                <option key={reason} value={reason}>
                  {reason}
                </option>
              ))
            ) : (
              <option value="">No reasons available</option>
            )}
          </select>
        </div>
        <div className="mt-5 flex justify-end gap-3">
          <button
            type="button"
            onClick={onClose}
            disabled={loading}
            className="rounded-lg border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 disabled:cursor-not-allowed disabled:opacity-60"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={loading || !selectedReason}
            className="rounded-lg bg-rose-500 px-4 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:bg-rose-300"
          >
            {loading ? "Working..." : "Confirm Flag"}
          </button>
        </div>
      </div>
    </div>
  );
}
