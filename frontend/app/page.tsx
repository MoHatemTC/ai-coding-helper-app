"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { getSessionToken } from "@/lib/auth";

export default function Home() {
  const router = useRouter();

  useEffect(() => {
    router.replace(getSessionToken() ? "/chat" : "/login");
  }, [router]);

  return null;
}
