"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import {
  createProfileQueryKey,
  getReaderProfile,
  submitOnboarding,
} from "@/lib/api/product";
import { localizeApiErrorMessage } from "@/lib/api/client";
import type { OnboardingPayload, ReaderProfile } from "@/lib/api/types";
import { getFeedSessionId, getSessionEventName, hasSession } from "@/lib/auth/session";

import { OnboardingStepLayout, PreferenceSelector } from "./primitives";

const genreOptions = [
  "Современная проза",
  "Детектив",
  "Фэнтези",
  "Историческая проза",
  "Научная фантастика",
  "Романтика",
  "Эссе",
  "Мемуары",
];

const goalOptions = [
  "Замедлиться перед сном",
  "Найти захватывающую книгу",
  "Читать больше современной прозы",
  "Найти комфортное чтение",
  "Поддерживать регулярную привычку читать",
  "Выйти за пределы привычного вкуса",
];

const moodOptions = ["Атмосферное", "Нежное", "Тревожное", "Обнадёживающее", "Меланхоличное", "Игривое"];
const toneOptions = ["Вдумчивое", "Тёплое", "Мрачное", "Элегантное", "Любопытное", "Точное"];
const styleOptions = [
  "О героях",
  "Сюжетное",
  "Лиричное",
  "Минималистичное",
  "Погружающее",
  "Диалоговое",
];
const atmosphereOptions = [
  "Тихие городки",
  "Старинные дома",
  "Туманные города",
  "Дикие побережья",
  "Академическая среда",
  "Интимные домашние пространства",
];
const plotOptions = [
  "Семейные тайны",
  "Второй шанс",
  "Расследования",
  "Взросление",
  "Медленно развивающиеся отношения",
  "Моральные дилеммы",
];
const authorSuggestions = [
  "Салли Руни",
  "Донна Тартт",
  "Кадзуо Исигуро",
  "Элена Ферранте",
  "Тана Френч",
  "Миэко Каваками",
];
const bookSuggestions = [
  "Тайная история",
  "Не отпускай меня",
  "Моя гениальная подруга",
  "Нормальные люди",
  "Пиранези",
  "Щегол",
];

const genreAliases: Record<string, string> = {
  "Literary fiction": "Современная проза",
  Mystery: "Детектив",
  Fantasy: "Фэнтези",
  "Historical fiction": "Историческая проза",
  "Science fiction": "Научная фантастика",
  Romance: "Романтика",
  Essays: "Эссе",
  Memoir: "Мемуары",
};

const goalAliases: Record<string, string> = {
  "Slow down before sleep": "Замедлиться перед сном",
  "Find a page-turner": "Найти захватывающую книгу",
  "Read more contemporary voices": "Читать больше современной прозы",
  "Discover comfort reads": "Найти комфортное чтение",
  "Keep a steady reading habit": "Поддерживать регулярную привычку читать",
  "Stretch beyond my usual taste": "Выйти за пределы привычного вкуса",
};

const moodAliases: Record<string, string> = {
  Atmospheric: "Атмосферное",
  Tender: "Нежное",
  Unsettling: "Тревожное",
  Hopeful: "Обнадёживающее",
  Melancholic: "Меланхоличное",
  Playful: "Игривое",
};

const toneAliases: Record<string, string> = {
  Thoughtful: "Вдумчивое",
  Warm: "Тёплое",
  Dark: "Мрачное",
  Elegant: "Элегантное",
  Curious: "Любопытное",
  Precise: "Точное",
};

const styleAliases: Record<string, string> = {
  "Character-led": "О героях",
  "Plot-driven": "Сюжетное",
  Lyrical: "Лиричное",
  Minimalist: "Минималистичное",
  Immersive: "Погружающее",
  "Dialog-heavy": "Диалоговое",
};

const atmosphereAliases: Record<string, string> = {
  "Quiet towns": "Тихие городки",
  "Grand houses": "Старинные дома",
  "Foggy cities": "Туманные города",
  "Wild coastlines": "Дикие побережья",
  "Academic circles": "Академическая среда",
  "Intimate domestic spaces": "Интимные домашние пространства",
};

const plotAliases: Record<string, string> = {
  "Family secrets": "Семейные тайны",
  "Second chances": "Второй шанс",
  Investigations: "Расследования",
  "Coming-of-age": "Взросление",
  "Slow-burn relationships": "Медленно развивающиеся отношения",
  "Moral dilemmas": "Моральные дилеммы",
};

type OnboardingState = {
  favoriteGenres: string[];
  favoriteAuthors: string[];
  favoriteBooks: string[];
  readingGoals: string[];
  favoriteMoods: string[];
  favoriteTone: string[];
  favoriteStyle: string[];
  favoriteAtmosphere: string[];
  favoritePlot: string[];
  customAuthors: string;
  customBooks: string;
};

function parseFreeformList(value: string) {
  return Array.from(
    new Set(
      value
        .split(",")
        .map((entry) => entry.trim())
        .filter(Boolean),
    ),
  );
}

function mergeSelectionWithFreeform(selected: string[], freeform: string) {
  return Array.from(new Set([...selected, ...parseFreeformList(freeform)]));
}

function normalizeSelectedValues(
  values: string[],
  options: string[],
  aliases: Record<string, string> = {},
) {
  return values
    .map((value) => aliases[value] ?? value)
    .filter((value) => options.includes(value));
}

function stateFromProfile(profile: ReaderProfile): OnboardingState {
  return {
    favoriteGenres: normalizeSelectedValues(profile.preferredGenres, genreOptions, genreAliases),
    favoriteAuthors: profile.preferredAuthors.filter((author) => authorSuggestions.includes(author)),
    favoriteBooks: profile.favoriteBooks.filter((book) => bookSuggestions.includes(book)),
    readingGoals: normalizeSelectedValues(profile.readingGoals, goalOptions, goalAliases),
    favoriteMoods: normalizeSelectedValues(profile.moods, moodOptions, moodAliases),
    favoriteTone: normalizeSelectedValues(profile.tones, toneOptions, toneAliases),
    favoriteStyle: normalizeSelectedValues(profile.styles, styleOptions, styleAliases),
    favoriteAtmosphere: normalizeSelectedValues(
      profile.atmosphere,
      atmosphereOptions,
      atmosphereAliases,
    ),
    favoritePlot: normalizeSelectedValues(profile.plotInterests, plotOptions, plotAliases),
    customAuthors: profile.preferredAuthors
      .filter((author) => !authorSuggestions.includes(author))
      .join(", "),
    customBooks: profile.favoriteBooks.filter((book) => !bookSuggestions.includes(book)).join(", "),
  };
}

function initialState(): OnboardingState {
  return {
    favoriteGenres: [],
    favoriteAuthors: [],
    favoriteBooks: [],
    readingGoals: [],
    favoriteMoods: [],
    favoriteTone: [],
    favoriteStyle: [],
    favoriteAtmosphere: [],
    favoritePlot: [],
    customAuthors: "",
    customBooks: "",
  };
}

function buildPayload(state: OnboardingState): OnboardingPayload {
  return {
    favorite_genres: state.favoriteGenres,
    favorite_authors: mergeSelectionWithFreeform(state.favoriteAuthors, state.customAuthors),
    favorite_books: mergeSelectionWithFreeform(state.favoriteBooks, state.customBooks),
    reading_goals: state.readingGoals,
    favorite_moods: state.favoriteMoods,
    favorite_tone: state.favoriteTone,
    favorite_style: state.favoriteStyle,
    favorite_atmosphere: state.favoriteAtmosphere,
    favorite_plot: state.favoritePlot,
  };
}

function validationMessage(step: number, state: OnboardingState) {
  const payload = buildPayload(state);

  if (step === 0 && payload.favorite_genres.length === 0) {
    return "Выбери хотя бы один жанр, чтобы задать первое направление рекомендаций.";
  }

  if (step === 1 && payload.favorite_authors.length + payload.favorite_books.length === 0) {
    return "Добавь хотя бы одного автора или одну книгу, чтобы лента получила понятные ориентиры.";
  }

  if (step === 2 && payload.reading_goals.length === 0) {
    return "Выбери хотя бы одну читательскую цель, чтобы сформировать первую подборку.";
  }

  if (
    step === 3 &&
    payload.favorite_moods.length + payload.favorite_tone.length + payload.favorite_style.length === 0
  ) {
    return "Перед завершением выбери хотя бы одно настроение, тон или стиль.";
  }

  return "";
}

function toggleSelection(values: string[], value: string) {
  return values.includes(value) ? values.filter((entry) => entry !== value) : [...values, value];
}

export function OnboardingFlow() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [sessionPresent, setSessionPresent] = useState(false);
  const [sessionChecked, setSessionChecked] = useState(false);
  const [step, setStep] = useState(0);
  const [state, setState] = useState<OnboardingState>(initialState);
  const [showValidation, setShowValidation] = useState(false);
  const [hydratedFromProfile, setHydratedFromProfile] = useState(false);
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

  useEffect(() => {
    if (sessionChecked && !sessionPresent) {
      router.replace("/login");
    }
  }, [router, sessionChecked, sessionPresent]);

  useEffect(() => {
    if (!profileQuery.data || hydratedFromProfile) {
      return;
    }

    setState(stateFromProfile(profileQuery.data));
    setHydratedFromProfile(true);
  }, [hydratedFromProfile, profileQuery.data]);

  const mutation = useMutation({
    mutationFn: submitOnboarding,
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: createProfileQueryKey(sessionId) }),
        queryClient.invalidateQueries({ queryKey: ["product", "feed"] }),
      ]);
      router.replace("/feed");
      router.refresh();
    },
  });

  function update<K extends keyof OnboardingState>(key: K, value: OnboardingState[K]) {
    setState((current) => ({
      ...current,
      [key]: value,
    }));
  }

  const totalSteps = 4;
  const currentValidation = validationMessage(step, state);

  function handleNext() {
    if (currentValidation) {
      setShowValidation(true);
      return;
    }

    if (step === totalSteps - 1) {
      mutation.mutate(buildPayload(state));
      return;
    }

    setStep((current) => current + 1);
    setShowValidation(false);
  }

  function handleBack() {
    setStep((current) => Math.max(0, current - 1));
    setShowValidation(false);
  }

  if (!sessionChecked || (profileQuery.isLoading && !hydratedFromProfile)) {
    return <div className="page-frame py-10 text-sm text-muted-foreground">Готовим твой профиль...</div>;
  }

  const sharedProps = {
    currentStep: step + 1,
    totalSteps,
    onBack: handleBack,
    onNext: handleNext,
    canGoBack: step > 0,
    nextLabel:
      step === totalSteps - 1
        ? profileQuery.data?.hasCompletedOnboarding
          ? "Обновить мою ленту"
          : "Собрать мою ленту"
        : "Продолжить",
    isSubmitting: mutation.isPending,
    validationMessage: showValidation
      ? currentValidation
      : mutation.error?.message
        ? localizeApiErrorMessage(mutation.error.message)
        : undefined,
  };

  return (
    <div className="page-frame py-8 sm:py-12">
      {step === 0 ? (
        <OnboardingStepLayout
          {...sharedProps}
          eyebrow="Читательское направление"
          title="Давай наметим полки, к которым ты возвращаешься."
          description="Начни с тех книг, которые уже кажутся своими. От этого мы оттолкнёмся в первой версии твоей ленты."
        >
          <PreferenceSelector
            label="Жанры"
            description="Выбери несколько широких направлений. Здесь можно не сдерживаться."
            options={genreOptions}
            selected={state.favoriteGenres}
            onToggle={(value) => update("favoriteGenres", toggleSelection(state.favoriteGenres, value))}
          />
        </OnboardingStepLayout>
      ) : null}

      {step === 1 ? (
        <OnboardingStepLayout
          {...sharedProps}
          eyebrow="Точки опоры"
          title="Дай Novellect несколько понятных ориентиров."
          description="Авторы и книги — самый простой способ быстро сделать первые рекомендации более личными."
        >
          <PreferenceSelector
            label="Любимые авторы"
            description="Выбери несколько подсказок или впиши своих авторов через запятую."
            options={authorSuggestions}
            selected={state.favoriteAuthors}
            onToggle={(value) => update("favoriteAuthors", toggleSelection(state.favoriteAuthors, value))}
            freeformValue={state.customAuthors}
            onFreeformChange={(value) => update("customAuthors", value)}
            freeformPlaceholder="Хилари Мантел, Ольга Токарчук, Клариси Лиспектор"
          />
          <PreferenceSelector
            label="Любимые книги"
            description="Пара названий поможет сделать ленту более точной уже с первого экрана."
            options={bookSuggestions}
            selected={state.favoriteBooks}
            onToggle={(value) => update("favoriteBooks", toggleSelection(state.favoriteBooks, value))}
            freeformValue={state.customBooks}
            onFreeformChange={(value) => update("customBooks", value)}
            freeformPlaceholder="Моя гениальная подруга, Левая рука тьмы"
          />
        </OnboardingStepLayout>
      ) : null}

      {step === 2 ? (
        <OnboardingStepLayout
          {...sharedProps}
          eyebrow="Цели чтения"
          title="Что ты хочешь получить от чтения прямо сейчас?"
          description="Это помогает задать первой подборке не только жанр, но и практический смысл."
        >
          <PreferenceSelector
            label="Цели чтения"
            description="Выбери то, что сейчас действительно хочется получить от книг."
            options={goalOptions}
            selected={state.readingGoals}
            onToggle={(value) => update("readingGoals", toggleSelection(state.readingGoals, value))}
          />
        </OnboardingStepLayout>
      ) : null}

      {step === 3 ? (
        <OnboardingStepLayout
          {...sharedProps}
          eyebrow="Тон и атмосфера"
          title="Теперь настроим настроение, тон и общий характер чтения."
          description="Эти сигналы делают первую ленту более точной и живой, чем обычный выбор жанра."
        >
          <PreferenceSelector
            label="Настроение"
            description="Какие чувства должны вызывать книги?"
            options={moodOptions}
            selected={state.favoriteMoods}
            onToggle={(value) => update("favoriteMoods", toggleSelection(state.favoriteMoods, value))}
          />
          <PreferenceSelector
            label="Тон"
            description="Каким должен быть голос текста?"
            options={toneOptions}
            selected={state.favoriteTone}
            onToggle={(value) => update("favoriteTone", toggleSelection(state.favoriteTone, value))}
          />
          <PreferenceSelector
            label="Стиль"
            description="Какой тип чтения тебе обычно ближе всего?"
            options={styleOptions}
            selected={state.favoriteStyle}
            onToggle={(value) => update("favoriteStyle", toggleSelection(state.favoriteStyle, value))}
          />
          <PreferenceSelector
            label="Атмосфера"
            description="Необязательно, но полезно, если ты уже понимаешь, в каком мире хочешь оказаться."
            options={atmosphereOptions}
            selected={state.favoriteAtmosphere}
            onToggle={(value) =>
              update("favoriteAtmosphere", toggleSelection(state.favoriteAtmosphere, value))
            }
          />
          <PreferenceSelector
            label="Интересы в сюжете"
            description="Дополнительные сигналы о том, какие сюжетные ходы тебе ближе."
            options={plotOptions}
            selected={state.favoritePlot}
            onToggle={(value) => update("favoritePlot", toggleSelection(state.favoritePlot, value))}
          />
        </OnboardingStepLayout>
      ) : null}
    </div>
  );
}
