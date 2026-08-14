import type { RoomSnapshot as RoomSnapshotType } from "../../lib/api-shape";
import { PercentBar } from "./PercentBar";

export function RoomSnapshot({ snapshot }: { snapshot: RoomSnapshotType }) {
  const hasSections = snapshot.sections.length > 0;

  return (
    <div className="rounded-xl border border-[#dbe3ee] bg-white p-5">
      <p className="text-xs font-semibold uppercase tracking-wide text-[#7c8aa0]">
        Room snapshot
      </p>

      {!hasSections && (
        <p className="mt-4 text-sm text-[#7c8aa0]">
          No one has been classified yet. Once the analysis finishes, a
          breakdown by field, role, and seniority will show up here.
        </p>
      )}

      <div className="mt-4 flex flex-col gap-6">
        {snapshot.sections.map((section) => (
          <div key={section.id}>
            <h3 className="text-base font-semibold text-[#091b36]">{section.title}</h3>
            <div className="mt-3 flex flex-col gap-2.5">
              {section.bars.map((bar) => (
                <PercentBar key={bar.key} bar={bar} />
              ))}
            </div>
          </div>
        ))}
      </div>

      <p className="mt-6 border-t border-[#eef2f8] pt-4 text-xs leading-5 text-[#94a3b8]">
        {snapshot.basis.disclaimer}
      </p>
    </div>
  );
}
