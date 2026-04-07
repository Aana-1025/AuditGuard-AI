export default function ProgressBar({ itemStatuses }) {
  const statusMap = itemStatuses || {};
  const totalItems = Object.keys(statusMap).length;
  const reviewedItems = Object.values(statusMap).filter(
    (status) => status !== "unreviewed"
  ).length;
  const progress = totalItems > 0 ? (reviewedItems / totalItems) * 100 : 0;

  let barColor = "bg-red-500";
  if (progress >= 50 && progress <= 80) {
    barColor = "bg-yellow-500";
  } else if (progress > 80) {
    barColor = "bg-green-500";
  }

  return (
    <div className="mb-5 rounded-2xl border border-white/50 bg-white/40 p-4 shadow-lg backdrop-blur-xl">
      <p className="text-sm font-medium text-gray-800">
        Reviewed {reviewedItems} / {totalItems} items
      </p>
      <div className="mt-2 h-3 w-full overflow-hidden rounded-full bg-white/70">
        <div
          className={`h-full rounded-full transition-all duration-500 ease-out ${barColor}`}
          style={{ width: `${Math.max(0, Math.min(100, progress))}%` }}
        />
      </div>
    </div>
  );
}
