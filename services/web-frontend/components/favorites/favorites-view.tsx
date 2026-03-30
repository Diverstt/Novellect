"use client";

import { useQueries, useQuery } from "@tanstack/react-query";
import { ArrowLeft, BookOpen, Heart, X } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { EmptyState, ErrorState, LoadingSkeleton, TagPill } from "@/components/product/primitives";
import { Button, buttonVariants } from "@/components/ui/button";
import {
  createProfileQueryKey,
  getBookContent,
  getBookPreview,
  getReaderProfile,
} from "@/lib/api/product";
import type { BookPreview } from "@/lib/api/types";
import { getFeedSessionId, getSessionEventName, hasSession } from "@/lib/auth/session";
import { cn } from "@/lib/utils";

export function FavoritesView() {
  const [sessionPresent, setSessionPresent] = useState(false);
  const [sessionChecked, setSessionChecked] = useState(false);
  const [readerBookId, setReaderBookId] = useState<string | null>(null);
  const sessionId = getFeedSessionId();

  useEffect(() => {
    const syncSession = () => setSessionPresent(hasSession());

    syncSession();
    setSessionChecked(true);
    window.addEventListener(getSessionEventName(), syncSession);
    window.addEventListener("focus", syncSession);

    return () => {
      window.removeEventListener(getSessionEventName(), syncSession);
      window.removeEventListener("focus", syncSession);
    };
  }, []);

  const profileQuery = useQuery({
    queryKey: createProfileQueryKey(sessionId),
    queryFn: () => getReaderProfile(sessionId),
    enabled: sessionChecked && sessionPresent,
    retry: false,
  });

  const bookIds = profileQuery.data?.savedBookIds ?? [];

  const previewQueries = useQueries({
    queries: bookIds.map((bookId) => ({
      queryKey: ["product", "favorite-preview", bookId],
      queryFn: () => getBookPreview(bookId),
      enabled: sessionChecked && sessionPresent,
      staleTime: 60_000,
    })),
  });

  const favoriteBooks = useMemo(
    () =>
      previewQueries
        .map((query, index) => ({ query, bookId: bookIds[index] }))
        .filter((entry): entry is { query: typeof previewQueries[number] & { data: BookPreview }; bookId: string } => Boolean(entry.query.data))
        .map((entry) => entry.query.data),
    [bookIds, previewQueries],
  );

  const readerQuery = useQuery({
    queryKey: ["product", "favorite-content", readerBookId ?? "none"],
    queryFn: () => getBookContent(readerBookId ?? ""),
    enabled: Boolean(readerBookId),
    staleTime: 60_000,
  });

  const isLoadingPreviews =
    profileQuery.isLoading || (bookIds.length > 0 && previewQueries.some((query) => query.isLoading));
  const hasPreviewError = previewQueries.some((query) => query.isError);

  if (!sessionChecked || (sessionPresent && profileQuery.isLoading)) {
    return (
      <div className="page-frame py-8 sm:py-12">
        <div className="mb-8 space-y-3">
          <div className="text-xs uppercase tracking-[0.24em] text-muted-foreground">
            Избранное
          </div>
          <h1 className="font-serif text-4xl leading-tight sm:text-5xl">
            Собираем сохранённые книги в отдельную полку.
          </h1>
        </div>
        <LoadingSkeleton variant="feed" />
      </div>
    );
  }

  if (!sessionPresent) {
    return (
      <div className="page-frame py-8 sm:py-12">
        <EmptyState
          title="Избранное доступно после входа."
          description="Войди в аккаунт, чтобы видеть книги, которые ты лайкнул в ленте, и читать их позже."
          actionLabel="Перейти ко входу"
          actionHref="/login"
        />
      </div>
    );
  }

  if (profileQuery.isError) {
    return (
      <div className="page-frame py-8 sm:py-12">
        <ErrorState
          title="Не удалось загрузить избранные книги."
          description="Попробуй обновить страницу. Профиль сохранён, просто сейчас не получилось получить список лайков."
          actionLabel="Повторить"
          onAction={() => profileQuery.refetch()}
        />
      </div>
    );
  }

  return (
    <>
      <div className="page-frame py-8 sm:py-12">
        <div className="mb-8 flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
          <div className="space-y-3">
            <div className="text-xs uppercase tracking-[0.24em] text-muted-foreground">
              Избранное
            </div>
            <h1 className="font-serif text-4xl leading-tight sm:text-5xl">
              Книги, которые уже тебе откликнулись
            </h1>
            <p className="max-w-3xl text-base leading-8 text-muted-foreground">
              Здесь лежат книги, которые пользователь лайкнул в ленте. Можно быстро вернуться к ним,
              открыть текст в полном окне и спокойно дочитать позже.
            </p>
          </div>
          <div className="flex flex-wrap gap-3">
            <div className="rounded-full border border-white/60 bg-card/80 px-4 py-2 text-sm text-muted-foreground">
              В избранном: {bookIds.length}
            </div>
            <Link href="/feed" className={buttonVariants({ variant: "secondary" })}>
              <ArrowLeft className="mr-2 h-4 w-4" />
              Вернуться в ленту
            </Link>
          </div>
        </div>

        {isLoadingPreviews ? (
          <LoadingSkeleton variant="feed" />
        ) : null}

        {!isLoadingPreviews && hasPreviewError ? (
          <div className="mb-6 rounded-[1.5rem] border border-destructive/20 bg-destructive/10 p-5 text-sm text-destructive">
            Не удалось загрузить часть книг из избранного. Остальное уже доступно, а выпавшие книги можно
            открыть после повторной загрузки страницы.
          </div>
        ) : null}

        {!isLoadingPreviews && favoriteBooks.length === 0 ? (
          <EmptyState
            title="Избранное пока пустое."
            description="Лайкни книгу в ленте, и она сразу появится здесь как на личной книжной полке."
            actionLabel="Открыть ленту"
            actionHref="/feed"
          />
        ) : null}

        {favoriteBooks.length > 0 ? (
          <div className="grid gap-5">
            {favoriteBooks.map((book) => (
              <article
                key={book.id}
                className="rounded-[2rem] border border-white/70 bg-card/90 p-5 shadow-editorial backdrop-blur sm:p-6"
              >
                <div className="flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
                  <div className="space-y-4">
                    <div className="flex flex-wrap gap-2">
                      <TagPill>{book.format.toUpperCase()}</TagPill>
                      <TagPill className="border-primary/20 bg-primary/10 text-primary">
                        <Heart className="mr-1.5 h-3.5 w-3.5" />
                        Лайкнуто
                      </TagPill>
                    </div>
                    <div>
                      <h2 className="font-serif text-3xl leading-tight text-foreground">{book.title}</h2>
                      {book.author ? (
                        <p className="mt-2 text-sm uppercase tracking-[0.18em] text-muted-foreground">
                          {book.author}
                        </p>
                      ) : null}
                    </div>
                    <p className="max-w-4xl text-base leading-8 text-muted-foreground">
                      {book.summary || book.previewExcerpt}
                    </p>
                    <div className="rounded-[1.5rem] border border-white/60 bg-background/70 p-4">
                      <div className="mb-2 text-xs uppercase tracking-[0.24em] text-muted-foreground">
                        Фрагмент книги
                      </div>
                      <p className="text-sm leading-7 text-foreground/85">
                        {book.previewExcerpt || "Для этой книги пока нет короткого фрагмента, но полный текст уже доступен."}
                      </p>
                    </div>
                  </div>

                  <div className="flex shrink-0 flex-col gap-3 lg:min-w-[220px]">
                    <Button type="button" className="gap-2" onClick={() => setReaderBookId(book.id)}>
                      <BookOpen className="h-4 w-4" />
                      Читать на весь экран
                    </Button>
                  </div>
                </div>
              </article>
            ))}
          </div>
        ) : null}
      </div>

      {readerBookId ? (
        <div className="fixed inset-0 z-50 flex h-screen w-screen items-stretch justify-stretch bg-background">
          <div className="flex h-full w-full flex-col overflow-hidden bg-background">
            <div className="flex items-start justify-between gap-4 border-b border-white/60 px-5 py-4 sm:px-8">
              <div className="space-y-2">
                <div className="text-xs uppercase tracking-[0.24em] text-muted-foreground">
                  Режим чтения
                </div>
                <h2 className="font-serif text-3xl leading-tight text-foreground">
                  {readerQuery.data?.title ?? favoriteBooks.find((book) => book.id === readerBookId)?.title ?? "Книга"}
                </h2>
                <div className="flex flex-wrap gap-2 text-sm text-muted-foreground">
                  {readerQuery.data?.author || favoriteBooks.find((book) => book.id === readerBookId)?.author ? (
                    <span>{readerQuery.data?.author || favoriteBooks.find((book) => book.id === readerBookId)?.author}</span>
                  ) : null}
                  <span>
                    {(readerQuery.data?.format ?? favoriteBooks.find((book) => book.id === readerBookId)?.format ?? "txt").toUpperCase()}
                  </span>
                  {typeof readerQuery.data?.contentLength === "number" ? (
                    <span>{readerQuery.data.contentLength.toLocaleString("ru-RU")} знаков</span>
                  ) : null}
                </div>
              </div>
              <Button type="button" variant="ghost" className="gap-2" onClick={() => setReaderBookId(null)}>
                <X className="h-4 w-4" />
                Закрыть
              </Button>
            </div>

            <div className="grid flex-1 gap-0 overflow-hidden lg:grid-cols-[320px_minmax(0,1fr)]">
              <aside className="border-b border-white/60 bg-card/60 px-5 py-5 lg:border-b-0 lg:border-r lg:px-6">
                <div className="space-y-4">
                  <div className="rounded-[1.5rem] border border-white/60 bg-background/70 p-4 text-sm leading-7 text-muted-foreground">
                    {readerQuery.data?.summary ||
                      favoriteBooks.find((book) => book.id === readerBookId)?.summary ||
                      favoriteBooks.find((book) => book.id === readerBookId)?.previewExcerpt ||
                      "Полный текст книги открыт в режиме чтения."}
                  </div>
                </div>
              </aside>

              <section className="overflow-y-auto px-5 py-5 sm:px-8">
                {readerQuery.isLoading ? (
                  <div className="space-y-3 text-sm text-muted-foreground">
                    <p>Подгружаем полный текст книги...</p>
                  </div>
                ) : null}
                {readerQuery.isError ? (
                  <div className="rounded-2xl border border-destructive/20 bg-destructive/10 px-4 py-3 text-sm text-destructive">
                    Не удалось открыть полный текст. Попробуй ещё раз через несколько секунд.
                  </div>
                ) : null}
                {readerQuery.data?.content ? (
                  <article className="whitespace-pre-wrap font-serif text-lg leading-8 text-foreground/95">
                    {readerQuery.data.content}
                  </article>
                ) : null}
                {!readerQuery.isLoading && !readerQuery.isError && !readerQuery.data?.content ? (
                  <div className="rounded-2xl border border-white/60 bg-card/80 px-4 py-3 text-sm text-muted-foreground">
                    Для этой книги пока нет полного текста, но карточка уже сохранена в избранном.
                  </div>
                ) : null}
              </section>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
