"use client";

import { useEffect } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className="page-frame py-10">
      <Card className="mx-auto max-w-xl">
        <CardHeader>
          <CardTitle className="font-serif text-3xl">Страница загрузилась с ошибкой</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="editorial-copy">
            Не удалось завершить загрузку страницы. Попробуй ещё раз.
          </p>
          <Button onClick={reset}>Повторить</Button>
        </CardContent>
      </Card>
    </div>
  );
}
