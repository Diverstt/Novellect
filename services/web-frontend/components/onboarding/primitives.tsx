"use client";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

type ProgressHeaderProps = {
  currentStep: number;
  totalSteps: number;
  title: string;
  description: string;
};

type OnboardingStepLayoutProps = {
  eyebrow: string;
  title: string;
  description: string;
  currentStep: number;
  totalSteps: number;
  children: React.ReactNode;
  onBack?: () => void;
  onNext?: () => void;
  nextLabel: string;
  isSubmitting?: boolean;
  canGoBack?: boolean;
  validationMessage?: string;
};

type PreferenceSelectorProps = {
  label: string;
  description: string;
  options: string[];
  selected: string[];
  onToggle: (value: string) => void;
  freeformValue?: string;
  onFreeformChange?: (value: string) => void;
  freeformPlaceholder?: string;
};

export function ProgressHeader({
  currentStep,
  totalSteps,
  title,
  description,
}: ProgressHeaderProps) {
  const progress = Math.round((currentStep / totalSteps) * 100);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-4">
        <div>
          <div className="text-xs uppercase tracking-[0.24em] text-muted-foreground">
            Шаг {currentStep} из {totalSteps}
          </div>
          <h1 className="mt-2 font-serif text-4xl leading-tight sm:text-5xl">{title}</h1>
        </div>
        <div className="rounded-full border border-white/60 bg-card/80 px-4 py-2 text-sm text-muted-foreground">
          {progress}%
        </div>
      </div>
      <p className="editorial-copy max-w-3xl">{description}</p>
      <div className="h-2 overflow-hidden rounded-full bg-secondary">
        <div
          className="h-full rounded-full bg-primary transition-all duration-300"
          style={{ width: `${progress}%` }}
        />
      </div>
    </div>
  );
}

export function OnboardingStepLayout({
  eyebrow,
  title,
  description,
  currentStep,
  totalSteps,
  children,
  onBack,
  onNext,
  nextLabel,
  isSubmitting = false,
  canGoBack = true,
  validationMessage,
}: OnboardingStepLayoutProps) {
  return (
    <div className="space-y-6">
      <ProgressHeader
        currentStep={currentStep}
        totalSteps={totalSteps}
        title={title}
        description={description}
      />
      <Card className="border-white/70 bg-card/90 shadow-editorial backdrop-blur">
        <CardHeader className="space-y-3">
          <span className="text-xs uppercase tracking-[0.24em] text-muted-foreground">
            {eyebrow}
          </span>
          <CardTitle className="font-serif text-2xl sm:text-3xl">{title}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-6">
          {children}
          {validationMessage ? (
            <p className="rounded-2xl border border-destructive/20 bg-destructive/10 px-4 py-3 text-sm text-destructive">
              {validationMessage}
            </p>
          ) : null}
          <div className="flex flex-col-reverse gap-3 sm:flex-row sm:justify-between">
            <Button
              type="button"
              variant="ghost"
              disabled={!canGoBack || isSubmitting}
              onClick={onBack}
            >
              Назад
            </Button>
            <Button type="button" className="w-full sm:w-auto" disabled={isSubmitting} onClick={onNext}>
              {isSubmitting ? "Сохраняем..." : nextLabel}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

export function PreferenceSelector({
  label,
  description,
  options,
  selected,
  onToggle,
  freeformValue,
  onFreeformChange,
  freeformPlaceholder,
}: PreferenceSelectorProps) {
  return (
    <div className="space-y-4">
      <div className="space-y-1">
        <h2 className="text-base font-medium text-foreground">{label}</h2>
        <p className="text-sm leading-6 text-muted-foreground">{description}</p>
      </div>
      <div className="flex flex-wrap gap-2">
        {options.map((option) => {
          const active = selected.includes(option);

          return (
            <button
              key={option}
              type="button"
              onClick={() => onToggle(option)}
              className={cn(
                "rounded-full border px-4 py-2 text-sm transition-colors",
                active
                  ? "border-primary bg-primary text-primary-foreground"
                  : "border-border bg-background/80 text-muted-foreground hover:bg-secondary",
              )}
            >
              {option}
            </button>
          );
        })}
      </div>
      {typeof freeformValue === "string" && onFreeformChange ? (
        <Input
          value={freeformValue}
          onChange={(event) => onFreeformChange(event.target.value)}
          placeholder={freeformPlaceholder}
        />
      ) : null}
    </div>
  );
}
