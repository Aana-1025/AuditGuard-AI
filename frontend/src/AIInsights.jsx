import { useMemo } from "react";

const normalize = (value) => String(value || "").trim().toLowerCase();

const getRiskLabel = (score) => {
  const numeric = Number(score);
  if (!Number.isFinite(numeric)) return "LOW";
  if (numeric < 0.3) return "LOW";
  if (numeric < 0.8) return "MEDIUM";
  return "HIGH";
};

const riskLevelFromBackend = (riskValue) => {
  if (typeof riskValue === "string") {
    const level = riskValue.trim().toUpperCase();
    if (level === "HIGH" || level === "MEDIUM" || level === "LOW") {
      return level;
    }
    return getRiskLabel(riskValue);
  }

  return getRiskLabel(riskValue);
};

const ICONS = {
  high: "\uD83D\uDEA8", // 🚨
  medium: "\u26A0\uFE0F", // ⚠️
  low: "\u2139\uFE0F", // ℹ️
  missingReceipt: "\uD83D\uDCC4", // 📄
  forbidden: "\u274C", // ❌
  split: "\uD83D\uDD01", // 🔁
};

export default function AIInsights({ items, itemStatuses, policy, riskByItem }) {
  const insights = useMemo(() => {
    const safeItems = Array.isArray(items) ? items : [];
    const safeStatuses = { ...(itemStatuses || {}) };
    const safePolicy = policy || {};
    const safeRiskByItem = { ...(riskByItem || {}) };

    for (let i = 0; i < safeItems.length; i += 1) {
      const item = safeItems[i];
      const itemId = item?.item_id ?? item?.id ?? item?.expense_id;
      if (!itemId) continue;
      if (!safeStatuses[itemId] && item?.status) {
        safeStatuses[itemId] = item.status;
      }
      if (safeRiskByItem[itemId] === undefined && item?.risk_score !== undefined) {
        safeRiskByItem[itemId] = item.risk_score;
      }
    }

    const receiptThreshold = Number(safePolicy.receipt_required_over_amount ?? 0);
    const forbiddenMerchants = new Set(
      (safePolicy.forbidden_merchants || []).map((merchant) => normalize(merchant))
    );

    const forbiddenCount = safeItems.filter((item) =>
      forbiddenMerchants.has(normalize(item?.merchant_descriptor || item?.merchant))
    ).length;

    const missingReceiptCount = safeItems.filter((item) => {
      const amount = Number(item?.submitted_amount ?? item?.amount ?? 0);
      return amount > receiptThreshold && item?.receipt_present === false;
    }).length;

    const approvedHighRiskCount = Object.entries(safeStatuses).filter(
      ([itemId, status]) =>
        status === "approved" &&
        riskLevelFromBackend(safeRiskByItem[itemId]) === "HIGH"
    ).length;

    const approvedMediumRiskCount = Object.entries(safeStatuses).filter(
      ([itemId, status]) =>
        status === "approved" &&
        riskLevelFromBackend(safeRiskByItem[itemId]) === "MEDIUM"
    ).length;

    const approvedLowRiskCount = Object.entries(safeStatuses).filter(
      ([itemId, status]) =>
        status === "approved" &&
        riskLevelFromBackend(safeRiskByItem[itemId]) === "LOW"
    ).length;

    const categoryCounts = {};
    for (const item of safeItems) {
      const category = item?.submitted_category || item?.category || "uncategorized";
      categoryCounts[category] = (categoryCounts[category] || 0) + 1;
    }

    const repeatedCategory = Object.entries(categoryCounts)
      .filter(([, count]) => count >= 3)
      .sort((a, b) => b[1] - a[1])[0];

    const generated = [];

    if (forbiddenCount > 1) {
      generated.push(`${ICONS.forbidden} ${forbiddenCount} transactions from forbidden merchants detected`);
    }

    if (missingReceiptCount > 0) {
      generated.push(`${ICONS.missingReceipt} ${missingReceiptCount} transactions missing receipts above threshold`);
    }

    if (repeatedCategory) {
      generated.push(
        `${ICONS.split} ${repeatedCategory[1]} ${repeatedCategory[0]} transactions may indicate splitting`
      );
    }

    generated.push(`${ICONS.high} ${approvedHighRiskCount} high-risk transactions approved`);
    generated.push(`${ICONS.medium} ${approvedMediumRiskCount} medium-risk transactions approved`);
    generated.push(`${ICONS.low} ${approvedLowRiskCount} low-risk transactions approved`);

    return generated;
  }, [items, itemStatuses, policy, riskByItem]);

  return (
    <div className="mb-6 rounded-xl border border-white/50 bg-white/40 p-4 shadow-lg backdrop-blur-xl transition-all duration-500">
      <h2 className="mb-2 bg-gradient-to-r from-cyan-700 via-blue-700 to-indigo-700 bg-clip-text text-lg font-semibold text-transparent">
        AI Insights
      </h2>
      <ul className="space-y-2">
        {insights.map((insight, index) => (
          <li key={`${insight}-${index}`} className="text-gray-700">
            {insight}
          </li>
        ))}
      </ul>
    </div>
  );
}
