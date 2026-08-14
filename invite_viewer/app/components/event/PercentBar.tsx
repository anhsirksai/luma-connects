import type { SnapshotBar } from "../../lib/api-shape";

export function PercentBar({ bar }: { bar: SnapshotBar }) {
  return (
    <div className="flex items-center gap-3 text-sm">
      <span className="w-36 shrink-0 text-[#334155]">{bar.label}</span>
      <div className="h-2 flex-1 overflow-hidden rounded-full bg-[#dbe8ff]">
        <div
          className="h-full rounded-full bg-[#3d7ffc]"
          style={{ width: `${Math.min(100, Math.max(0, bar.percentage))}%` }}
        />
      </div>
      <span className="w-10 shrink-0 text-right font-semibold text-[#091b36]">
        {bar.percentage}%
      </span>
    </div>
  );
}
