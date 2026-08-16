"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { logoutAdmin } from "../../lib/api";

export function LogoutButton() {
  const router = useRouter();
  const [pending, setPending] = useState(false);

  return (
    <button
      type="button"
      disabled={pending}
      onClick={async () => {
        setPending(true);
        await logoutAdmin();
        router.push("/login");
        router.refresh();
      }}
      className="text-sm font-semibold text-[#5b6b82] hover:text-[#091b36] disabled:opacity-60"
    >
      {pending ? "..." : "Log out"}
    </button>
  );
}
