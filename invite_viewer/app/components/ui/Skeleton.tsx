export function Skeleton({ className = "" }: { className?: string }) {
  return (
    <div
      className={`animate-pulse rounded-md bg-[#e3e9f2] ${className}`}
      aria-hidden="true"
    />
  );
}
