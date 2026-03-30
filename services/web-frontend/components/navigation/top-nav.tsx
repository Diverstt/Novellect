"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { getCurrentUser } from "@/lib/api/auth";
import { createProfileQueryKey, getReaderProfile } from "@/lib/api/product";
import { clearSession, getFeedSessionId, getSessionEventName, hasSession } from "@/lib/auth/session";
import { cn } from "@/lib/utils";

export function TopNav() {
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

  const meQuery = useQuery({
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
        { href: "/", label: "Главная" },
        { href: "/onboarding", label: "Онбординг" },
        { href: "/feed", label: "Лента" },
        { href: "/favorites", label: "Избранное" },
      ]
    : [
        { href: "/", label: "Главная" },
        { href: "/login", label: "Вход" },
        { href: "/register", label: "Регистрация" },
      ];

  function handleSignOut() {
    clearSession();
    queryClient.clear();
    router.push("/login");
    router.refresh();
  }

  return (
    <header className="fixed inset-x-0 top-0 z-40 border-b border-white/60 bg-background/80 backdrop-blur-xl">
      <div className="page-frame flex h-16 items-center justify-between gap-4">
        <Link href="/" className="min-w-0">
          <div className="font-serif text-2xl tracking-tight text-foreground">Novellect</div>
          <div className="hidden text-xs uppercase tracking-[0.24em] text-muted-foreground sm:block">
            Интеллектуальная система рекомендаций книг
          </div>
        </Link>
        <nav className="hidden items-center gap-1 md:flex">
          {navItems.map((item) => {
            const active = pathname === item.href;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={cn(
                  "rounded-full px-4 py-2 text-sm text-muted-foreground hover:bg-secondary hover:text-secondary-foreground",
                  active && "bg-primary text-primary-foreground hover:bg-primary",
                )}
              >
                {item.label}
              </Link>
            );
          })}
          {sessionPresent ? (
            <button
              type="button"
              onClick={handleSignOut}
              className="rounded-full px-4 py-2 text-sm text-muted-foreground hover:bg-secondary hover:text-secondary-foreground"
            >
              Выйти
            </button>
          ) : null}
        </nav>
        <div className="min-w-[120px] text-right text-xs text-muted-foreground">
          {meQuery.isLoading && sessionPresent ? "Проверяем сессию..." : null}
          {meQuery.isError && sessionPresent ? "Нужно обновить сессию" : null}
          {profileQuery.isLoading && sessionPresent ? "Загружаем профиль вкуса..." : null}
          {meQuery.data?.user ? (
            <div>
              <div className="font-medium text-foreground">
                {meQuery.data.user.display_name || "Читатель"}
              </div>
              <div className="truncate">{meQuery.data.user.email}</div>
              <div>
                {profileQuery.data?.hasCompletedOnboarding ? "Лента готова" : "Онбординг не завершён"}
              </div>
            </div>
          ) : null}
          {!sessionPresent ? "Ты не вошёл в аккаунт" : null}
        </div>
      </div>
    </header>
  );
}
