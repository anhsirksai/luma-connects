import type { SnapshotBar } from "../../lib/api-shape";
import { fieldLabel } from "../../lib/format";

export function CategoryStrip({ bars }: { bars: SnapshotBar[] }) {
  if (bars.length === 0) {
    return (
      <p className="text-xs text-[#7c8aa0]">
        Room breakdown not available yet.
      </p>
    );
  }

  return (
    <div className="flex flex-wrap gap-2">
      {bars.map((bar) => (
        <span
          key={bar.key}
          className="inline-flex items-center gap-1.5 rounded-full bg-[#eef2f8] px-3 py-1 text-xs font-medium text-[#334155]"
        >
          <strong className="font-semibold text-[#091b36]">{bar.percentage}%</strong>
          {fieldLabel(bar.label || bar.key).toLowerCase()}
        </span>
      ))}
    </div>
  );
}
