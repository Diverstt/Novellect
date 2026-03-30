"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { BookHeart, Compass, Home, LogIn, LogOut, UserPlus } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { getCurrentUser } from "@/lib/api/auth";
import { createProfileQueryKey, getReaderProfile } from "@/lib/api/product";
import { clearSession, getFeedSessionId, getSessionEventName, hasSession } from "@/lib/auth/session";
import { cn } from "@/lib/utils";

export function MobileBottomNav() {
  const pathname = usePathname();
  const router = useRouter();
  const queryClient = useQueryClient();
  const [sessionPresent, setSessionPresent] = useState(false);
  const sessionId = getFeedSessionId();

  useEffect(() => {
    const syncSession = () => setSessionPresent(hasSession());

    syncSession();
    window.addEventListener(getSessionEventName(), syncSession);
    window.addEventListener("focus", syncSession);

    return () => {
      window.removeEventListener(getSessionEventName(), syncSession);
      window.removeEventListener("focus", syncSession);
    };
  }, []);

  useQuery({
    queryKey: ["auth", "me"],
    queryFn: getCurrentUser,
    enabled: sessionPresent,
    retry: false,
  });

  const profileQuery = useQuery({
    queryKey: createProfileQueryKey(sessionId),
    queryFn: () => getReaderProfile(sessionId),
    enabled: sessionPresent,
    retry: false,
  });

  const navItems = sessionPresent
    ? [
        { href: "/", label: "Главная", icon: Home },
        { href: "/onboarding", label: "Старт", icon: Compass },
        { href: "/feed", label: "Лента", icon: BookHeart },
        { href: "/favorites", label: "Избр.", icon: BookHeart },
      ]
    : [
        { href: "/", label: "Главная", icon: Home },
        { href: "/login", label: "Вход", icon: LogIn },
        { href: "/register", label: "Рег.", icon: UserPlus },
      ];

  function handleSignOut() {
    clearSession();
    queryClient.clear();
    router.push("/login");
    router.refresh();
  }

  return (
    <nav className="fixed inset-x-0 bottom-0 z-40 border-t border-white/60 bg-background/95 backdrop-blur md:hidden">
      <div
        className={cn(
          "mx-auto grid max-w-lg px-2 py-2",
          sessionPresent ? "grid-cols-5" : "grid-cols-3",
        )}
      >
        {navItems.map((item) => {
          const active = pathname === item.href;
          const Icon = item.icon;
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "flex flex-col items-center justify-center gap-1 rounded-2xl px-3 py-2 text-xs text-muted-foreground",
                active && "bg-primary text-primary-foreground",
              )}
            >
              <Icon className="h-4 w-4" />
              <span>{item.label}</span>
            </Link>
          );
        })}
        {sessionPresent ? (
          <button
            type="button"
            onClick={handleSignOut}
            className="flex flex-col items-center justify-center gap-1 rounded-2xl px-3 py-2 text-xs text-muted-foreground"
          >
            <LogOut className="h-4 w-4" />
            <span>Выйти</span>
          </button>
        ) : null}
      </div>
    </nav>
  );
}
