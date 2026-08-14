const TONE_CLASSES: Record<string, string> = {
  confirmed: "bg-[#dff4ea] text-[#136c46] border-[#bfe6d3]",
  inferred: "bg-[#fff2d6] text-[#8a5a00] border-[#f3dfa8]",
  neutral: "bg-[#eef2f8] text-[#42536b] border-[#dbe3ee]",
  accent: "bg-[#dbe8ff] text-[#0a3d91] border-[#b9d3ff]",
};

export function Pill({
  children,
  tone = "neutral",
}: {
  children: React.ReactNode;
  tone?: keyof typeof TONE_CLASSES;
}) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs font-semibold ${TONE_CLASSES[tone]}`}
    >
      {children}
    </span>
  );
}
