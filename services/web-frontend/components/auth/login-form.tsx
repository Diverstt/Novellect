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
import { login } from "@/lib/api/auth";
import { resolvePostAuthPath } from "@/lib/api/product";
import { ApiError, localizeApiErrorMessage } from "@/lib/api/client";
import { saveSession } from "@/lib/auth/session";

export function LoginForm() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  const mutation = useMutation({
    mutationFn: login,
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
          Вход
        </span>
        <CardTitle className="font-serif text-3xl">Вернись к своей читательской полке</CardTitle>
        <p className="editorial-copy">
          Войди в аккаунт Novellect, чтобы продолжить работу с текущей сессией.
        </p>
      </CardHeader>
      <CardContent>
        <form
          className="space-y-5"
          onSubmit={(event) => {
            event.preventDefault();
            mutation.mutate({ email, password });
          }}
        >
          <Alert>
            <AlertTitle>Эта форма доступна всегда</AlertTitle>
            <AlertDescription>
              Если ты уже вошёл в другой аккаунт, новый вход просто заменит текущую сессию.
            </AlertDescription>
          </Alert>
          <div className="space-y-2">
            <Label htmlFor="login-email">Электронная почта</Label>
            <Input
              id="login-email"
              type="email"
              autoComplete="email"
              placeholder="reader@novellect.app"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="login-password">Пароль</Label>
            <Input
              id="login-password"
              type="password"
              autoComplete="current-password"
              placeholder="Не меньше 6 символов"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            />
          </div>
          {mutation.isError ? (
            <Alert variant="destructive">
              <AlertTitle>Не удалось войти</AlertTitle>
              <AlertDescription>
                {mutation.error instanceof ApiError
                  ? localizeApiErrorMessage(mutation.error.message)
                  : "Проверь данные и попробуй ещё раз."}
              </AlertDescription>
            </Alert>
          ) : null}
          <Button type="submit" className="w-full" disabled={mutation.isPending}>
            {mutation.isPending ? "Входим..." : "Войти"}
          </Button>
          <p className="text-sm text-muted-foreground">
            Впервые здесь?{" "}
            <Link href="/register" className="text-foreground underline-offset-4 hover:underline">
              Создать аккаунт
            </Link>
          </p>
        </form>
      </CardContent>
    </Card>
  );
}
