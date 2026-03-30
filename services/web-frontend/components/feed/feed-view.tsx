"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { FeedCard } from "@/components/product/feed-card";
import { EmptyState, ErrorState, LoadingSkeleton, TagPill } from "@/components/product/primitives";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  createFeedQueryKey,
  createProfileQueryKey,
  getReaderProfile,
  getRecommendationFeed,
  isOnboardingComplete,
  postInteraction,
} from "@/lib/api/product";
import { localizeApiErrorMessage } from "@/lib/api/client";
import type { InteractionAction, RecommendationFeed } from "@/lib/api/types";
import { getFeedSessionId, getSessionEventName, hasSession } from "@/lib/auth/session";

type PendingInteraction = {
  bookId: string;
  action: InteractionAction;
} | null;

type RecommendationContext = "profile" | "query";

function applyOptimisticInteraction(
  current: RecommendationFeed | undefined,
  variables: PendingInteraction,
) {
  if (!current || !variables) {
    return current;
  }

  return {
    ...current,
    count: Math.max(0, current.count - 1),
    items: current.items.filter((item) => item.id !== variables.bookId),
  };
}

function appendUniqueId(current: string[], nextId: string) {
  if (!nextId || current.includes(nextId)) {
    return current;
  }
  return [...current, nextId];
}

export function FeedView() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [sessionPresent, setSessionPresent] = useState(false);
  const [sessionChecked, setSessionChecked] = useState(false);
  const [actionError, setActionError] = useState("");
  const [pendingInteraction, setPendingInteraction] = useState<PendingInteraction>(null);
  const [draftQuery, setDraftQuery] = useState("");
  const [activeQuery, setActiveQuery] = useState("");
  const [dismissedBookIds, setDismissedBookIds] = useState<string[]>([]);
  const [recommendationContext, setRecommendationContext] =
    useState<RecommendationContext>("profile");
  const sessionId = getFeedSessionId();
  const profileKey = createProfileQueryKey(sessionId);
  const normalizedQuery = activeQuery.trim();
  const feedKey = createFeedQueryKey({
    recommendationContext,
    mode: "similar",
    limit: 3,
    sessionId,
    query: normalizedQuery,
  });

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

  useEffect(() => {
    if (sessionChecked && !sessionPresent) {
      router.replace("/login");
    }
  }, [router, sessionChecked, sessionPresent]);

  const profileQuery = useQuery({
    queryKey: profileKey,
    queryFn: () => getReaderProfile(sessionId),
    enabled: sessionChecked && sessionPresent,
    retry: false,
  });
  const profileAvailable = Boolean(profileQuery.data?.hasCompletedOnboarding);

  useEffect(() => {
    if (!profileQuery.data) {
      return;
    }
    if (!isOnboardingComplete(profileQuery.data) && recommendationContext === "profile") {
      setRecommendationContext("query");
    }
  }, [profileQuery.data, recommendationContext]);

  const feedEnabled =
    sessionChecked &&
    sessionPresent &&
    ((recommendationContext === "profile" && Boolean(profileQuery.data?.hasCompletedOnboarding)) ||
      (recommendationContext === "query" && normalizedQuery.length > 0));

  const feedQuery = useQuery({
    queryKey: feedKey,
    queryFn: () =>
      getRecommendationFeed({
        recommendationContext,
        mode: "similar",
        limit: 3,
        sessionId,
        query: recommendationContext === "query" ? normalizedQuery || undefined : undefined,
      }),
    enabled: feedEnabled,
  });

  useEffect(() => {
    setDismissedBookIds([]);
  }, [feedQuery.data?.items?.map((item) => item.id).join("|")]);

  const interactionMutation = useMutation({
    mutationFn: async (variables: PendingInteraction) => {
      if (!variables) {
        throw new Error("Missing interaction payload");
      }

      return postInteraction({
        book_id: variables.bookId,
        action: variables.action,
        session_id: sessionId,
        source: "web_feed",
        metadata:
          recommendationContext === "query"
            ? { active_query: normalizedQuery, recommendation_context: "query" }
            : { recommendation_context: "profile" },
      });
    },
    onMutate: async (variables) => {
      setActionError("");
      setPendingInteraction(variables);
      await queryClient.cancelQueries({ queryKey: feedKey });
      const previousFeed = queryClient.getQueryData<RecommendationFeed>(feedKey);

      queryClient.setQueryData(feedKey, applyOptimisticInteraction(previousFeed, variables));

      return { previousFeed };
    },
    onError: (error, _variables, context) => {
      if (context?.previousFeed) {
        queryClient.setQueryData(feedKey, context.previousFeed);
      }
      setActionError(
        error instanceof Error
          ? localizeApiErrorMessage(error.message)
          : "Не удалось сохранить это действие.",
      );
    },
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: profileKey }),
        queryClient.invalidateQueries({ queryKey: feedKey }),
      ]);
    },
    onSettled: () => {
      setPendingInteraction(null);
    },
  });

  if (!sessionChecked || profileQuery.isLoading || (feedEnabled && feedQuery.isLoading)) {
    return (
      <div className="page-frame py-8 sm:py-12">
        <div className="mb-8 space-y-3">
          <div className="text-xs uppercase tracking-[0.24em] text-muted-foreground">
            Персональная лента
          </div>
          <h1 className="font-serif text-4xl leading-tight sm:text-5xl">
            Собираем первую подборку под твой вкус.
          </h1>
        </div>
        <LoadingSkeleton variant="feed" />
      </div>
    );
  }

  if (profileQuery.isError) {
    return (
      <div className="page-frame py-8 sm:py-12">
        <ErrorState
          title="Не удалось загрузить твой читательский профиль."
          description="Обнови страницу или войди заново, чтобы продолжить работу с персональной лентой."
          actionLabel="Повторить"
          onAction={() => profileQuery.refetch()}
        />
      </div>
    );
  }

  if (feedEnabled && feedQuery.isError) {
    return (
      <div className="page-frame py-8 sm:py-12">
        <ErrorState
          title="Лента временно недоступна."
          description="Сейчас не удалось получить рекомендации, но аккаунт и онбординг сохранены."
          actionLabel="Обновить ленту"
          onAction={() => feedQuery.refetch()}
        />
      </div>
    );
  }

  if (!feedEnabled && recommendationContext === "profile") {
    return (
      <div className="page-frame py-8 sm:py-12">
        <EmptyState
          title="Персональная лента появится после онбординга."
          description="Чтобы включить персональные рекомендации, закончи онбординг. Либо перейди в режим поиска по запросу."
          actionLabel="Открыть онбординг"
          actionHref="/onboarding"
        />
      </div>
    );
  }

  if (feedEnabled && (!feedQuery.data || feedQuery.data.items.length === 0)) {
    return (
      <div className="page-frame py-8 sm:py-12">
        <EmptyState
          title="Под запрос пока не нашлось подходящих книг."
          description={
            normalizedQuery
              ? "Попробуй переформулировать запрос, убрать слишком узкие детали или сменить режим рекомендаций."
              : "Попробуй добавить запрос своими словами или обнови онбординг, чтобы сузить первую подборку."
          }
          actionLabel={normalizedQuery ? "Сбросить запрос" : "Вернуться в онбординг"}
          onAction={normalizedQuery ? () => setActiveQuery("") : undefined}
          actionHref={normalizedQuery ? undefined : "/onboarding"}
        />
      </div>
    );
  }

  const profile = profileQuery.data;
  const feed = feedEnabled ? feedQuery.data : undefined;
  const visibleItems = (feed?.items ?? []).filter((item) => !dismissedBookIds.includes(item.id));
  const currentItem = visibleItems[0];
  const contextTags =
    recommendationContext === "profile"
      ? [
          ...(profile?.preferredGenres.slice(0, 2) ?? []),
          ...(profile?.moods.slice(0, 2) ?? []),
          ...(profile?.readingGoals.slice(0, 1) ?? []),
        ]
      : [];

  return (
    <div className="page-frame py-8 sm:py-12">
      <section className="space-y-6">
        <div className="space-y-4">
          <span className="text-xs uppercase tracking-[0.24em] text-muted-foreground">
            {recommendationContext === "query" ? "Поиск по запросу" : "Персональная лента"}
          </span>
          <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
            <div className="space-y-3">
              <h1 className="max-w-4xl font-serif text-4xl leading-tight sm:text-5xl">
                {recommendationContext === "query"
                  ? "Сформулируй запрос и получи отдельную выдачу под текущую задачу."
                  : "Листай персональные рекомендации по одной книге."}
              </h1>
              <p className="editorial-copy max-w-3xl">
                {recommendationContext === "query"
                  ? "В этом режиме выдача строится по текущему запросу и не зависит от анкеты как от главного сигнала."
                  : "В этом режиме лента опирается на онбординг, накопленный профиль и недавние реакции на книги."}
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              {profileAvailable ? (
                <Button
                  type="button"
                  variant={recommendationContext === "profile" ? "default" : "secondary"}
                  className="rounded-full"
                  onClick={() => {
                    setRecommendationContext("profile");
                    setActionError("");
                    setPendingInteraction(null);
                    setDismissedBookIds([]);
                  }}
                >
                  Персональная лента
                </Button>
              ) : null}
              <Button
                type="button"
                variant={recommendationContext === "query" ? "default" : "secondary"}
                className="rounded-full"
                onClick={() => {
                  setRecommendationContext("query");
                  setActionError("");
                  setPendingInteraction(null);
                  setDismissedBookIds([]);
                }}
              >
                Поиск по запросу
              </Button>
              <Link
                href="/onboarding"
                className="rounded-full border border-white/60 bg-card/80 px-4 py-3 text-sm text-foreground hover:bg-secondary"
              >
                {profileAvailable ? "Изменить онбординг" : "Пройти онбординг"}
              </Link>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            {contextTags.map((tag) => (
              <TagPill key={tag}>{tag}</TagPill>
            ))}
            {normalizedQuery ? <TagPill className="border-primary/30 bg-primary/10">Запрос: {normalizedQuery}</TagPill> : null}
            <TagPill className="border-primary/30 bg-primary/10">
              Режим: {recommendationContext === "query" ? "query" : "profile"}
            </TagPill>
          </div>
          <form
            className="rounded-[1.75rem] border border-white/60 bg-card/85 p-4 shadow-editorial"
            onSubmit={(event) => {
              event.preventDefault();
              const nextQuery = draftQuery.trim();
              setRecommendationContext("query");
              setActiveQuery(nextQuery);
              setActionError("");
              setPendingInteraction(null);
              setDismissedBookIds([]);
              if (nextQuery === normalizedQuery) {
                void feedQuery.refetch();
              }
            }}
          >
            <div className="space-y-3">
              <div className="text-xs uppercase tracking-[0.24em] text-muted-foreground">
                Запрос для поиска
              </div>
              <p className="text-sm leading-7 text-muted-foreground">
                Опиши настроение, тему, автора, темп или пример книги. Например: «хочу
                психологический роман с мрачной атмосферой без мистики».
              </p>
              <div className="flex flex-col gap-3 lg:flex-row">
                <Input
                  value={draftQuery}
                  onChange={(event) => setDraftQuery(event.target.value)}
                  placeholder="Что тебе хочется почитать прямо сейчас?"
                  className="h-12 flex-1"
                />
                <Button type="submit" className="h-12 px-6">
                  Подобрать по запросу
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  className="h-12 px-6"
                  onClick={() => {
                    setDraftQuery("");
                    setActiveQuery("");
                    setActionError("");
                    setPendingInteraction(null);
                    setDismissedBookIds([]);
                    if (profileAvailable) {
                      setRecommendationContext("profile");
                    }
                  }}
                >
                  Сбросить
                </Button>
              </div>
            </div>
          </form>
          {actionError ? (
            <div className="rounded-2xl border border-destructive/20 bg-destructive/10 px-4 py-3 text-sm text-destructive">
              {actionError}
            </div>
          ) : null}
          {!feedEnabled && recommendationContext === "query" ? (
            <div className="rounded-[1.75rem] border border-white/60 bg-card/85 p-4 text-sm text-muted-foreground shadow-editorial">
              Введи запрос выше, и мы соберём отдельную выдачу без обязательного онбординга.
            </div>
          ) : null}
        </div>
        <div className="space-y-4">
          <div className="flex items-center justify-between gap-4 rounded-full border border-white/60 bg-card/80 px-4 py-3 text-sm text-muted-foreground">
            <span>Сейчас показываем одну карточку за раз, чтобы отклик был точнее.</span>
            <span>В очереди: {visibleItems.length}</span>
          </div>
          {currentItem ? (
            <FeedCard
              key={currentItem.id}
              recommendation={currentItem}
              sessionId={sessionId}
              query={normalizedQuery || undefined}
              pendingAction={
                pendingInteraction?.bookId === currentItem.id ? pendingInteraction.action : null
              }
              onAction={(bookId, action) => {
                if (action === "skip") {
                  setDismissedBookIds((current) => appendUniqueId(current, bookId));
                  setActionError("");
                  return;
                }
                interactionMutation.mutate({ bookId, action });
              }}
            />
          ) : null}
        </div>
      </section>
    </div>
  );
}
