import Link from "next/link";

import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export default function HomePage() {
  return (
    <div className="page-frame py-8 sm:py-12">
      <section className="mx-auto max-w-4xl">
        <div className="space-y-5 text-center">
          <span className="inline-flex rounded-full border bg-card/80 px-3 py-1 text-xs uppercase tracking-[0.24em] text-muted-foreground">
            Основа интерфейса Novellect
          </span>
          <h1 className="mx-auto max-w-3xl font-serif text-4xl leading-tight text-foreground sm:text-6xl">
            Спокойный и понятный вход в сервис для читателя.
          </h1>
          <p className="editorial-copy mx-auto max-w-2xl">
            Здесь уже собраны все твои любимые книги
          </p>
          <div className="flex flex-col justify-center gap-3 sm:flex-row">
            <Link href="/register" className={cn(buttonVariants(), "w-full sm:w-auto")}>
              Создать аккаунт
            </Link>
            <Link
              href="/login"
              className={cn(
                buttonVariants({ variant: "secondary" }),
                "w-full sm:w-auto",
              )}
            >
              Войти
            </Link>
          </div>
        </div>
      </section>
    </div>
  );
}
