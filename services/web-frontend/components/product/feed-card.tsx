"use client";

import { useQuery } from "@tanstack/react-query";
import {
  BookOpen,
  ChevronDown,
  ChevronUp,
  ExternalLink,
  Heart,
  SkipForward,
  X,
} from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { getBookContent, getRecommendationExplanation } from "@/lib/api/product";
import type { InteractionAction, Recommendation } from "@/lib/api/types";
import { cn } from "@/lib/utils";

import { RecommendationReason, TagPill } from "./primitives";

type FeedCardProps = {
  recommendation: Recommendation;
  sessionId: string;
  query?: string;
  pendingAction?: InteractionAction | null;
  onAction: (bookId: string, action: InteractionAction) => void;
};

export function FeedCard({
  recommendation,
  sessionId,
  query,
  pendingAction,
  onAction,
}: FeedCardProps) {
  const [expanded, setExpanded] = useState(false);
  const [readerOpen, setReaderOpen] = useState(false);

  const explanationQuery = useQuery({
    queryKey: ["product", "explain", recommendation.id, sessionId, query ?? ""],
    queryFn: () =>
      getRecommendationExplanation({
        bookId: recommendation.id,
        mode: recommendation.mode,
        recommendationContext: recommendation.recommendationContext,
        sessionId,
        query,
      }),
    enabled: expanded,
    staleTime: 60_000,
  });

  const contentQuery = useQuery({
    queryKey: ["product", "content", recommendation.id],
    queryFn: () => getBookContent(recommendation.id),
    enabled: readerOpen,
    staleTime: 60_000,
  });

  const detailedReason = explanationQuery.data?.item?.reason;
  const detailedCaveats = explanationQuery.data?.item?.caveats ?? recommendation.caveats;
  const detailedMatchReasons =
    explanationQuery.data?.item?.matchReasons ?? recommendation.matchReasons;

  const actions: Array<{
    action: InteractionAction;
    label: string;
    icon: React.ComponentType<{ className?: string }>;
    variant: "default" | "secondary" | "ghost";
  }> = [
    { action: "like", label: "Нравится", icon: Heart, variant: "default" },
    { action: "skip", label: "Пропустить", icon: SkipForward, variant: "ghost" },
  ];

  return (
    <Card className="border-white/70 bg-card/90 shadow-editorial backdrop-blur">
      <CardHeader className="space-y-4">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="space-y-2">
            <span className="text-xs uppercase tracking-[0.24em] text-muted-foreground">
              {recommendation.recommendationContext === "query"
                ? "Рекомендация по запросу"
                : "Персональная рекомендация"}
            </span>
            <CardTitle className="max-w-3xl font-serif text-3xl leading-tight sm:text-4xl">
              {recommendation.title}
            </CardTitle>
            {recommendation.author ? (
              <p className="text-sm uppercase tracking-[0.18em] text-muted-foreground">
                {recommendation.author}
              </p>
            ) : null}
            <div className="flex flex-wrap gap-2">
              <TagPill>{recommendation.format.toUpperCase()}</TagPill>
              {recommendation.matchReasons.slice(0, 2).map((reason) => (
                <TagPill key={reason}>{reason}</TagPill>
              ))}
            </div>
          </div>
          <div className="rounded-full border border-white/60 bg-background/80 px-4 py-2 text-sm text-muted-foreground">
            Оценка {recommendation.score.toFixed(2)}
          </div>
        </div>
        <p className="editorial-copy max-w-3xl">
          {recommendation.summary || recommendation.previewExcerpt}
        </p>
        <div className="flex flex-wrap gap-2">
          {recommendation.tags.slice(0, 6).map((tag) => (
            <TagPill key={tag}>{tag}</TagPill>
          ))}
        </div>
      </CardHeader>
      <CardContent className="space-y-5">
        <RecommendationReason
          summary={recommendation.reason}
          matchReasons={detailedMatchReasons}
          caveats={detailedCaveats}
          details={expanded ? detailedReason : undefined}
          isLoadingDetails={expanded && explanationQuery.isLoading}
        />
        {expanded ? (
          <div className="rounded-[1.5rem] border border-white/60 bg-background/70 p-4">
            <div className="mb-2 text-xs uppercase tracking-[0.24em] text-muted-foreground">
              Фрагмент книги
            </div>
            <p className="text-sm leading-7 text-muted-foreground">
              {recommendation.previewExcerpt || recommendation.summary}
            </p>
          </div>
        ) : null}
        <div className="grid grid-cols-2 gap-3 sm:flex sm:flex-wrap">
          {actions.map((item) => {
            const Icon = item.icon;
            const isPending = pendingAction === item.action;

            return (
              <Button
                key={item.action}
                type="button"
                variant={item.variant}
                className={cn("w-full gap-2 sm:w-auto")}
                disabled={Boolean(pendingAction)}
                onClick={() => onAction(recommendation.id, item.action)}
              >
                <Icon className="h-4 w-4" />
                {isPending ? "Обрабатываем..." : item.label}
              </Button>
            );
          })}
          <Button
            type="button"
            variant="secondary"
            className="w-full gap-2 sm:w-auto"
            onClick={() => setExpanded((current) => !current)}
          >
            <BookOpen className="h-4 w-4" />
            {expanded ? "Свернуть" : "Открыть"}
            {expanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
          </Button>
          <Button
            type="button"
            variant="secondary"
            className="w-full gap-2 sm:w-auto"
            onClick={() => setReaderOpen(true)}
          >
            <ExternalLink className="h-4 w-4" />
            Читать
          </Button>
        </div>
      </CardContent>
      {readerOpen ? (
        <div className="fixed inset-0 z-50 flex h-screen w-screen items-stretch justify-stretch bg-background">
          <div className="flex h-full w-full flex-col overflow-hidden bg-background">
            <div className="flex items-start justify-between gap-4 border-b border-white/60 px-5 py-4 sm:px-8">
              <div className="space-y-2">
                <div className="text-xs uppercase tracking-[0.24em] text-muted-foreground">
                  Режим чтения
                </div>
                <h2 className="font-serif text-3xl leading-tight text-foreground">
                  {contentQuery.data?.title ?? recommendation.title}
                </h2>
                <div className="flex flex-wrap gap-2 text-sm text-muted-foreground">
                  {contentQuery.data?.author || recommendation.author ? (
                    <span>{contentQuery.data?.author || recommendation.author}</span>
                  ) : null}
                  <span>{(contentQuery.data?.format ?? recommendation.format).toUpperCase()}</span>
                  {typeof contentQuery.data?.contentLength === "number" ? (
                    <span>{contentQuery.data.contentLength.toLocaleString("ru-RU")} знаков</span>
                  ) : null}
                </div>
              </div>
              <Button type="button" variant="ghost" className="gap-2" onClick={() => setReaderOpen(false)}>
                <X className="h-4 w-4" />
                Закрыть
              </Button>
            </div>

            <div className="grid flex-1 gap-0 overflow-hidden lg:grid-cols-[320px_minmax(0,1fr)]">
              <aside className="border-b border-white/60 bg-card/60 px-5 py-5 lg:border-b-0 lg:border-r lg:px-6">
                <div className="space-y-4">
                  <div className="flex flex-wrap gap-2">
                    {recommendation.tags.slice(0, 8).map((tag) => (
                      <TagPill key={tag}>{tag}</TagPill>
                    ))}
                  </div>
                  <div className="space-y-2">
                    <div className="text-xs uppercase tracking-[0.24em] text-muted-foreground">
                      О книге
                    </div>
                    <p className="text-sm leading-7 text-muted-foreground">
                      {contentQuery.data?.summary || recommendation.summary || recommendation.previewExcerpt}
                    </p>
                  </div>
                </div>
              </aside>

              <section className="overflow-y-auto px-5 py-5 sm:px-8">
                {contentQuery.isLoading ? (
                  <div className="space-y-3 text-sm text-muted-foreground">
                    <p>Подгружаем полный текст книги...</p>
                  </div>
                ) : null}
                {contentQuery.isError ? (
                  <div className="rounded-2xl border border-destructive/20 bg-destructive/10 px-4 py-3 text-sm text-destructive">
                    Не удалось открыть полный текст. Попробуй ещё раз через несколько секунд.
                  </div>
                ) : null}
                {contentQuery.data?.content ? (
                  <article className="whitespace-pre-wrap font-serif text-lg leading-8 text-foreground/95">
                    {contentQuery.data.content}
                  </article>
                ) : null}
                {!contentQuery.isLoading && !contentQuery.isError && !contentQuery.data?.content ? (
                  <div className="rounded-2xl border border-white/60 bg-card/80 px-4 py-3 text-sm text-muted-foreground">
                    Для этой книги пока нет полного текста, но краткое содержание и фрагмент уже
                    доступны в карточке.
                  </div>
                ) : null}
              </section>
            </div>
          </div>
        </div>
      ) : null}
    </Card>
  );
}
