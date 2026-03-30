"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { resolvePostAuthPath } from "@/lib/api/product";
import { register } from "@/lib/api/auth";
import { ApiError, localizeApiErrorMessage } from "@/lib/api/client";
import { saveSession } from "@/lib/auth/session";

export function RegisterForm() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  const mutation = useMutation({
    mutationFn: register,
    onSuccess: async (response) => {
      queryClient.clear();
      saveSession(response);
      const destination = await resolvePostAuthPath();
      router.replace(destination);
      router.refresh();
    },
  });

  return (
    <Card className="mx-auto max-w-lg border-white/70 bg-card/90 shadow-editorial backdrop-blur">
      <CardHeader className="space-y-3">
        <span className="text-xs uppercase tracking-[0.24em] text-muted-foreground">
          Регистрация
        </span>
        <CardTitle className="font-serif text-3xl">Создай аккаунт Novellect</CardTitle>
        <p className="editorial-copy">
          На этом шаге создаётся только аккаунт. Онбординг и персональная лента будут дальше.
        </p>
      </CardHeader>
      <CardContent>
        <form
          className="space-y-5"
          onSubmit={(event) => {
            event.preventDefault();
            mutation.mutate({ display_name: displayName, email, password });
          }}
        >
          <Alert>
            <AlertTitle>Можно создать ещё один аккаунт</AlertTitle>
            <AlertDescription>
              Текущая активная сессия не блокирует регистрацию. После отправки формы приложение
              переключится на нового пользователя.
            </AlertDescription>
          </Alert>
          <div className="space-y-2">
            <Label htmlFor="register-name">Имя в приложении</Label>
            <Input
              id="register-name"
              autoComplete="nickname"
              placeholder="Ада Читатель"
              value={displayName}
              onChange={(event) => setDisplayName(event.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="register-email">Электронная почта</Label>
            <Input
              id="register-email"
              type="email"
              autoComplete="email"
              placeholder="reader@novellect.app"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="register-password">Пароль</Label>
            <Input
              id="register-password"
              type="password"
              autoComplete="new-password"
              placeholder="Не меньше 6 символов"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              minLength={6}
              required
            />
          </div>
          {mutation.isError ? (
            <Alert variant="destructive">
              <AlertTitle>Не удалось создать аккаунт</AlertTitle>
              <AlertDescription>
                {mutation.error instanceof ApiError
                  ? localizeApiErrorMessage(mutation.error.message)
                  : "Проверь форму и попробуй ещё раз."}
              </AlertDescription>
            </Alert>
          ) : null}
          <Button type="submit" className="w-full" disabled={mutation.isPending}>
            {mutation.isPending ? "Создаём аккаунт..." : "Создать аккаунт"}
          </Button>
          <p className="text-sm text-muted-foreground">
            Уже есть аккаунт?{" "}
            <Link href="/login" className="text-foreground underline-offset-4 hover:underline">
              Войти
            </Link>
          </p>
        </form>
      </CardContent>
    </Card>
  );
}
