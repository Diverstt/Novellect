"use client";

import { useQueries, useQuery } from "@tanstack/react-query";
import { BookOpen, X } from "lucide-react";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { getBookContent, getBookPreview } from "@/lib/api/product";
import type { BookPreview } from "@/lib/api/types";
import { cn } from "@/lib/utils";

type FavoritesDialogProps = {
  bookIds: string[];
  open: boolean;
  onClose: () => void;
};

export function FavoritesDialog({ bookIds, open, onClose }: FavoritesDialogProps) {
  const [readerBookId, setReaderBookId] = useState<string | null>(null);

  const previewQueries = useQueries({
    queries: bookIds.map((bookId) => ({
      queryKey: ["product", "favorite-preview", bookId],
      queryFn: () => getBookPreview(bookId),
      enabled: open,
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

  if (!open) {
    return null;
  }

  const isLoading = previewQueries.some((query) => query.isLoading);
  const hasError = previewQueries.some((query) => query.isError);

  return (
    <>
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 p-3 sm:p-6">
        <div className="flex h-[90vh] w-full max-w-5xl flex-col overflow-hidden rounded-[2rem] border border-white/60 bg-background shadow-editorial">
          <div className="flex items-start justify-between gap-4 border-b border-white/60 px-5 py-4 sm:px-8">
            <div className="space-y-2">
              <div className="text-xs uppercase tracking-[0.24em] text-muted-foreground">
                Избранное
              </div>
              <h2 className="font-serif text-3xl leading-tight text-foreground">
                Сохранённые книги
              </h2>
              <p className="max-w-2xl text-sm leading-7 text-muted-foreground">
                Здесь лежат книги, которые ты отметил лайком или явно сохранил, чтобы вернуться к ним позже.
              </p>
            </div>
            <Button type="button" variant="ghost" className="gap-2" onClick={onClose}>
              <X className="h-4 w-4" />
              Закрыть
            </Button>
          </div>

          <div className="flex-1 overflow-y-auto px-5 py-5 sm:px-8">
            {isLoading ? (
              <div className="rounded-[1.5rem] border border-white/60 bg-card/70 p-5 text-sm text-muted-foreground">
                Загружаем список избранных книг...
              </div>
            ) : null}

            {hasError ? (
              <div className="rounded-[1.5rem] border border-destructive/20 bg-destructive/10 p-5 text-sm text-destructive">
                Не удалось загрузить часть избранных книг. Попробуй открыть окно ещё раз через несколько секунд.
              </div>
            ) : null}

            {!isLoading && favoriteBooks.length === 0 ? (
              <div className="rounded-[1.5rem] border border-white/60 bg-card/70 p-6 text-sm text-muted-foreground">
                В избранном пока пусто. Отмечай книги лайком в ленте, и они будут появляться здесь.
              </div>
            ) : null}

            {favoriteBooks.length > 0 ? (
              <div className="grid gap-4">
                {favoriteBooks.map((book) => (
                  <article
                    key={book.id}
                    className="rounded-[1.75rem] border border-white/60 bg-card/80 p-5 shadow-editorial"
                  >
                    <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                      <div className="space-y-3">
                        <div className="flex flex-wrap gap-2">
                          <span className="rounded-full border border-white/60 bg-background/80 px-3 py-1 text-xs uppercase tracking-[0.18em] text-muted-foreground">
                            {book.format.toUpperCase()}
                          </span>
                          <span className="rounded-full border border-primary/20 bg-primary/10 px-3 py-1 text-xs uppercase tracking-[0.18em] text-primary">
                            Сохранено
                          </span>
                        </div>
                        <div>
                          <h3 className="font-serif text-2xl leading-tight text-foreground">{book.title}</h3>
                          {book.author ? (
                            <p className="mt-1 text-sm uppercase tracking-[0.18em] text-muted-foreground">
                              {book.author}
                            </p>
                          ) : null}
                        </div>
                        <p className="max-w-3xl text-sm leading-7 text-muted-foreground">
                          {book.summary || book.previewExcerpt}
                        </p>
                        <p className="max-w-3xl text-sm leading-7 text-foreground/80">
                          {book.previewExcerpt || "Для этой книги пока нет отдельного фрагмента, но текст можно открыть целиком."}
                        </p>
                      </div>

                      <div className="flex shrink-0 flex-col gap-3">
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
        </div>
      </div>

      {readerBookId ? (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/55 p-3 sm:p-6">
          <div className="flex h-[92vh] w-full max-w-6xl flex-col overflow-hidden rounded-[2rem] border border-white/60 bg-background shadow-editorial">
            <div className="flex items-start justify-between gap-4 border-b border-white/60 px-5 py-4 sm:px-8">
              <div className="space-y-2">
                <div className="text-xs uppercase tracking-[0.24em] text-muted-foreground">
                  Полный текст
                </div>
                <h2 className="font-serif text-3xl leading-tight text-foreground">
                  {readerQuery.data?.title ?? favoriteBooks.find((book) => book.id === readerBookId)?.title ?? "Книга"}
                </h2>
                <div className="flex flex-wrap gap-2 text-sm text-muted-foreground">
                  {readerQuery.data?.author ? <span>{readerQuery.data.author}</span> : null}
                  <span>{(readerQuery.data?.format ?? favoriteBooks.find((book) => book.id === readerBookId)?.format ?? "txt").toUpperCase()}</span>
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

            <div className={cn("flex-1 overflow-y-auto px-5 py-5 sm:px-8")}>
              {readerQuery.isLoading ? (
                <div className="rounded-[1.5rem] border border-white/60 bg-card/70 p-5 text-sm text-muted-foreground">
                  Подгружаем полный текст книги...
                </div>
              ) : null}
              {readerQuery.isError ? (
                <div className="rounded-[1.5rem] border border-destructive/20 bg-destructive/10 p-5 text-sm text-destructive">
                  Не удалось открыть книгу. Попробуй ещё раз через несколько секунд.
                </div>
              ) : null}
              {readerQuery.data?.content ? (
                <article className="whitespace-pre-wrap font-serif text-lg leading-8 text-foreground/95">
                  {readerQuery.data.content}
                </article>
              ) : null}
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
