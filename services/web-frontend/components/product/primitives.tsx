"use client";

import Link from "next/link";

import { buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

type TagPillProps = {
  children: React.ReactNode;
  className?: string;
};

type EmptyStateProps = {
  title: string;
  description: string;
  actionLabel?: string;
  actionHref?: string;
  onAction?: () => void;
};

type ErrorStateProps = {
  title: string;
  description: string;
  actionLabel?: string;
  onAction?: () => void;
};

type LoadingSkeletonProps = {
  variant?: "feed" | "panel";
};

type RecommendationReasonProps = {
  summary: string;
  matchReasons?: string[];
  caveats?: string[];
  details?: string;
  isLoadingDetails?: boolean;
};

export function TagPill({ children, className }: TagPillProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border border-white/60 bg-secondary/80 px-3 py-1 text-[11px] uppercase tracking-[0.16em] text-muted-foreground",
        className,
      )}
    >
      {children}
    </span>
  );
}

export function EmptyState({
  title,
  description,
  actionLabel,
  actionHref,
  onAction,
}: EmptyStateProps) {
  const action = actionLabel
    ? actionHref
      ? (
          <Link href={actionHref} className={buttonVariants({ variant: "secondary" })}>
            {actionLabel}
          </Link>
        )
      : (
          <button
            type="button"
            onClick={onAction}
            className={buttonVariants({ variant: "secondary" })}
          >
            {actionLabel}
          </button>
        )
    : null;

  return (
    <Card className="border-white/70 bg-card/90">
      <CardHeader className="space-y-3">
        <span className="text-xs uppercase tracking-[0.24em] text-muted-foreground">
          Пока пусто
        </span>
        <CardTitle className="font-serif text-3xl">{title}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-5">
        <p className="editorial-copy">{description}</p>
        {action}
      </CardContent>
    </Card>
  );
}

export function ErrorState({ title, description, actionLabel, onAction }: ErrorStateProps) {
  return (
    <Card className="border-destructive/20 bg-card/95">
      <CardHeader className="space-y-3">
        <span className="text-xs uppercase tracking-[0.24em] text-destructive/80">
          Что-то пошло не так
        </span>
        <CardTitle className="font-serif text-3xl">{title}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-5">
        <p className="editorial-copy">{description}</p>
        {actionLabel ? (
          <button type="button" onClick={onAction} className={buttonVariants()}>
            {actionLabel}
          </button>
        ) : null}
      </CardContent>
    </Card>
  );
}

export function LoadingSkeleton({ variant = "feed" }: LoadingSkeletonProps) {
  if (variant === "panel") {
    return (
      <Card className="border-white/70 bg-card/90">
        <CardHeader className="space-y-3">
          <Skeleton className="h-4 w-24" />
          <Skeleton className="h-10 w-2/3" />
          <Skeleton className="h-5 w-full" />
        </CardHeader>
        <CardContent className="space-y-3">
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-12 w-3/4" />
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="grid gap-4">
      {Array.from({ length: 3 }).map((_, index) => (
        <Card key={index} className="border-white/70 bg-card/85">
          <CardHeader className="space-y-3">
            <Skeleton className="h-4 w-20" />
            <Skeleton className="h-10 w-3/4" />
            <Skeleton className="h-5 w-full" />
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex flex-wrap gap-2">
              <Skeleton className="h-7 w-20" />
              <Skeleton className="h-7 w-24" />
              <Skeleton className="h-7 w-16" />
            </div>
            <Skeleton className="h-20 w-full" />
            <div className="grid grid-cols-2 gap-3 sm:flex sm:flex-wrap">
              <Skeleton className="h-11 w-full sm:w-24" />
              <Skeleton className="h-11 w-full sm:w-24" />
              <Skeleton className="h-11 w-full sm:w-24" />
              <Skeleton className="h-11 w-full sm:w-24" />
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

export function RecommendationReason({
  summary,
  matchReasons = [],
  caveats = [],
  details,
  isLoadingDetails = false,
}: RecommendationReasonProps) {
  return (
    <div className="space-y-3 rounded-[1.5rem] border border-white/60 bg-background/70 p-4">
      <div className="space-y-2">
        <div className="text-xs uppercase tracking-[0.24em] text-muted-foreground">
          Почему это подходит
        </div>
        <p className="text-sm leading-7 text-foreground/90">{summary}</p>
      </div>
      {matchReasons.length > 0 ? (
        <div className="flex flex-wrap gap-2">
          {matchReasons.slice(0, 4).map((reason) => (
            <TagPill key={reason}>{reason}</TagPill>
          ))}
        </div>
      ) : null}
      {isLoadingDetails ? <Skeleton className="h-12 w-full" /> : null}
      {details ? <p className="text-sm leading-7 text-muted-foreground">{details}</p> : null}
      {caveats.length > 0 ? (
        <div className="space-y-2 rounded-2xl bg-secondary/60 p-3 text-sm text-muted-foreground">
          <div className="text-xs uppercase tracking-[0.2em]">Обрати внимание</div>
          <ul className="space-y-1">
            {caveats.slice(0, 2).map((caveat) => (
              <li key={caveat}>{caveat}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
