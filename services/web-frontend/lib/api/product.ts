import { apiFetch } from "@/lib/api/client";
import type {
  BookPreview,
  BookContent,
  InteractionInput,
  InteractionResult,
  OnboardingPayload,
  OnboardingResult,
  PreferenceScore,
  RawBookContentResponse,
  RawBookPreviewResponse,
  RawInteractionResponse,
  RawOnboardingResponse,
  RawProfileResponse,
  RawRecommendationExplainResponse,
  RawRecommendationFeedResponse,
  RawRecommendationItem,
  RawTasteProfile,
  ReaderProfile,
  Recommendation,
  RecommendationExplanation,
  RecommendationFeed,
} from "@/lib/api/types";

type FeedQueryParams = {
  mode?: "similar" | "new" | "risk";
  searchContext?: "profile" | "query";
  recommendationContext?: "profile" | "query";
  limit?: number;
  sessionId?: string;
  seedBookId?: string;
  query?: string;
  excludeBookIds?: string[];
  cursor?: string;
  offset?: number;
};

type ExplainQueryParams = {
  bookId: string;
  mode?: string;
  searchContext?: "profile" | "query";
  recommendationContext?: "profile" | "query";
  sessionId?: string;
  seedBookId?: string;
  query?: string;
  excludeBookIds?: string[];
};

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function normalizeString(value: unknown) {
  return typeof value === "string" ? value.trim() : "";
}

function unique(values: string[]) {
  return Array.from(new Set(values.map((value) => value.trim()).filter(Boolean)));
}

function normalizeStringArray(value: unknown) {
  if (!Array.isArray(value)) {
    return [];
  }

  return unique(value.map((entry) => normalizeString(entry)));
}

function toTitleCase(value: string) {
  return value
    .split(/\s+/)
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

function splitSlugWords(value: string) {
  return value
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function transliterateSlugWord(value: string) {
  let text = value.toLowerCase();
  const multiCharRules: Array<[RegExp, string]> = [
    [/shch/g, "щ"],
    [/sch/g, "щ"],
    [/yo/g, "ё"],
    [/yu/g, "ю"],
    [/ya/g, "я"],
    [/zh/g, "ж"],
    [/kh/g, "х"],
    [/ts/g, "ц"],
    [/ch/g, "ч"],
    [/sh/g, "ш"],
    [/iy\b/g, "ий"],
    [/yi\b/g, "ый"],
    [/yy\b/g, "ый"],
    [/ii\b/g, "ий"],
    [/oi\b/g, "ой"],
    [/ei\b/g, "ей"],
    [/ai\b/g, "ай"],
  ];

  for (const [pattern, replacement] of multiCharRules) {
    text = text.replace(pattern, replacement);
  }

  const charMap: Record<string, string> = {
    a: "а",
    b: "б",
    c: "к",
    d: "д",
    e: "е",
    f: "ф",
    g: "г",
    h: "х",
    i: "и",
    j: "й",
    k: "к",
    l: "л",
    m: "м",
    n: "н",
    o: "о",
    p: "п",
    q: "к",
    r: "р",
    s: "с",
    t: "т",
    u: "у",
    v: "в",
    w: "в",
    x: "кс",
    y: "й",
    z: "з",
  };

  text = Array.from(text)
    .map((char) => charMap[char] ?? char)
    .join("");

  return text
    .replace(/ийи\b/g, "ий")
    .replace(/ыи\b/g, "ы")
    .replace(/скийи\b/g, "ский")
    .replace(/кый\b/g, "кий")
    .replace(/йа/g, "я")
    .replace(/йу/g, "ю");
}

function transliterateSlugText(value: string) {
  return toTitleCase(
    splitSlugWords(value)
      .split(" ")
      .map((word) => transliterateSlugWord(word))
      .join(" "),
  );
}

const authorNameMap: Record<string, string> = {
  Belyaev: "Беляев",
  Bulgakov: "Булгаков",
  Bulichev: "Булычев",
  CharlotteBronte: "Шарлотта Бронте",
  Dostoevskiyi: "Достоевский",
  Dreiser: "Драйзер",
  Gogol: "Гоголь",
  Gorkiyi: "Горький",
  Gugo: "Гюго",
  Kuprin: "Куприн",
  Lermontov: "Лермонтов",
  MaminSibiryak: "Мамин-Сибиряк",
  Pasternak: "Пастернак",
  Sholohov: "Шолохов",
  Tolstoy: "Толстой",
  Turgenev: "Тургенев",
};

function normalizeRecommendationPresentation(rawTitle: unknown, rawAuthor: unknown) {
  const title = normalizeString(rawTitle);
  const author = normalizeString(rawAuthor);
  if (!title) {
    return { title: "Рекомендация без названия", author };
  }

  if (author || !/^[A-Za-z0-9_-]+$/.test(title) || !title.includes("_")) {
    return { title, author };
  }

  const [authorSlug, ...titleParts] = title.split("_").filter(Boolean);
  if (!authorSlug || titleParts.length === 0) {
    return { title: transliterateSlugText(title), author };
  }

  const normalizedAuthor = authorNameMap[authorSlug] ?? transliterateSlugText(authorSlug);
  const normalizedTitle = transliterateSlugText(titleParts.join(" "));
  return {
    title: normalizedTitle || title,
    author: normalizedAuthor || author,
  };
}

function normalizeReason(value: string, title: string, recommendationContext: "profile" | "query") {
  const normalized = value.trim();
  if (!normalized) {
    return recommendationContext === "query"
      ? "Подобрано под текущий запрос."
      : "Подобрано под твое направление чтения и недавние предпочтения.";
  }

  const lowerReason = normalized.toLowerCase();
  const lowerTitle = title.trim().toLowerCase();
  if (lowerTitle && lowerReason.startsWith(lowerTitle)) {
    const withoutTitle = normalized.slice(title.length).trimStart();
    if (withoutTitle.startsWith("может")) {
      return `Книга ${withoutTitle}`;
    }
  }

  return normalized;
}

function normalizeExcerpt(value: unknown) {
  let normalized = normalizeString(value).replace(/\s+/g, " ").trim();
  if (!normalized) {
    return "";
  }

  normalized = normalized.replace(/^\.\.\.(\S)/, "... $1").replace(/\s*\.{4,}/g, "...");

  if (/^\.\.\.\s*[а-яёa-z]{1,10}\s+/iu.test(normalized)) {
    normalized = normalized.replace(/^(\.\.\.)\s*[а-яёa-z]{1,10}\s+/iu, "$1 ");
  } else if (/^[а-яёa-z]{1,10}\s+/iu.test(normalized)) {
    normalized = normalized.replace(/^[а-яёa-z]{1,10}\s+/iu, "");
  }

  return normalized.trim();
}

function normalizePriorityList(value: unknown): PreferenceScore[] {
  if (!Array.isArray(value)) {
    return [];
  }

  return value
    .map((entry) => {
      if (Array.isArray(entry)) {
        const [label, score] = entry;
        return {
          label: normalizeString(label),
          score: typeof score === "number" ? score : Number(score ?? 0),
        };
      }

      return {
        label: normalizeString(entry),
        score: 0,
      };
    })
    .filter((entry) => entry.label);
}

function normalizeSearchContext(value: unknown, fallback: "profile" | "query") {
  return normalizeString(value) === "query" ? "query" : fallback;
}

function normalizeRequestMode(
  value: unknown,
  searchContext: "profile" | "query",
  query: string,
): "profile_only" | "query_only" | "profile_with_query" {
  const normalized = normalizeString(value);
  if (
    normalized === "profile_only" ||
    normalized === "query_only" ||
    normalized === "profile_with_query"
  ) {
    return normalized;
  }
  if (searchContext === "query") {
    return "query_only";
  }
  return query.trim() ? "profile_with_query" : "profile_only";
}

function normalizePriorities(value: unknown) {
  const record = asRecord(value);

  return {
    mood: normalizePriorityList(record.mood),
    tone: normalizePriorityList(record.tone),
    style: normalizePriorityList(record.style),
    atmosphere: normalizePriorityList(record.atmosphere),
    plot: normalizePriorityList(record.plot),
    characters: normalizePriorityList(record.characters),
  };
}

function profileOnboardingRecord(raw: RawProfileResponse | RawTasteProfile | null | undefined) {
  const record = asRecord(raw);
  const direct = asRecord(record.onboarding);

  if (Object.keys(direct).length > 0) {
    return direct;
  }

  return asRecord(asRecord(record.taste_profile).onboarding);
}

function hasMeaningfulValues(record: Record<string, unknown>) {
  return Object.values(record).some((value) => {
    if (Array.isArray(value)) {
      return value.some((entry) => normalizeString(entry));
    }

    if (typeof value === "string") {
      return value.trim().length > 0;
    }

    if (value && typeof value === "object") {
      return Object.keys(asRecord(value)).length > 0;
    }

    return Boolean(value);
  });
}

function collectOnboardingList(record: Record<string, unknown>, ...keys: string[]) {
  return unique(keys.flatMap((key) => normalizeStringArray(record[key])));
}

function buildProfile(raw: RawProfileResponse | RawTasteProfile | null | undefined, warning?: string) {
  const record = asRecord(raw);
  const tasteProfile = asRecord(record.taste_profile);
  const onboarding = profileOnboardingRecord(raw as RawProfileResponse | RawTasteProfile);
  const priorities = normalizePriorities(record.priorities ?? tasteProfile.priorities);

  const profile: ReaderProfile = {
    userId: normalizeString(record.user_id) || normalizeString(tasteProfile.user_id),
    onboarding,
    preferredGenres: unique([
      ...normalizeStringArray(record.preferred_genres),
      ...normalizeStringArray(tasteProfile.preferred_genres),
      ...collectOnboardingList(onboarding, "favorite_genres"),
    ]),
    preferredAuthors: unique([
      ...normalizeStringArray(record.preferred_authors),
      ...normalizeStringArray(tasteProfile.preferred_authors),
      ...collectOnboardingList(onboarding, "favorite_authors"),
    ]),
    favoriteBooks: unique([
      ...normalizeStringArray(record.favorite_book_ids),
      ...normalizeStringArray(tasteProfile.favorite_book_ids),
      ...collectOnboardingList(onboarding, "favorite_books"),
    ]),
    savedBookIds: unique([
      ...normalizeStringArray(record.saved_books),
      ...normalizeStringArray(record.favorite_book_ids),
      ...normalizeStringArray(tasteProfile.saved_books),
      ...normalizeStringArray(tasteProfile.favorite_book_ids),
    ]),
    readingGoals: collectOnboardingList(onboarding, "reading_goals"),
    moods: unique([
      ...collectOnboardingList(onboarding, "favorite_moods"),
      ...priorities.mood.map((entry) => entry.label),
    ]),
    tones: unique([
      ...collectOnboardingList(onboarding, "favorite_tone"),
      ...priorities.tone.map((entry) => entry.label),
    ]),
    styles: unique([
      ...collectOnboardingList(onboarding, "favorite_style"),
      ...priorities.style.map((entry) => entry.label),
    ]),
    atmosphere: unique([
      ...collectOnboardingList(onboarding, "favorite_atmosphere"),
      ...priorities.atmosphere.map((entry) => entry.label),
    ]),
    plotInterests: unique([
      ...collectOnboardingList(onboarding, "favorite_plot"),
      ...priorities.plot.map((entry) => entry.label),
    ]),
    priorities,
    interactionCounters: asRecord(
      record.interaction_counters ?? tasteProfile.interaction_counters,
    ) as Record<string, number>,
    hasCompletedOnboarding: hasMeaningfulValues(onboarding),
    warning,
  };

  return profile;
}

function buildRecommendation(item: RawRecommendationItem): Recommendation {
  const tags = unique(normalizeStringArray(item.style_tags));
  const presentation = normalizeRecommendationPresentation(item.title, item.author);
  const recommendationContext =
    normalizeString(item.recommendation_context) === "query" ? "query" : "profile";
  const searchContext = normalizeSearchContext(item.search_context, recommendationContext);
  const requestMode = normalizeRequestMode(item.request_mode, searchContext, "");
  const reason = normalizeReason(
    normalizeString(item.why_for_you) ||
      normalizeString(item.explanation) ||
      (recommendationContext === "query"
        ? "Подобрано под текущий запрос."
        : "Подобрано под твое направление чтения и недавние предпочтения."),
    presentation.title,
    recommendationContext,
  );

  return {
    id: normalizeString(item.book_id),
    title: presentation.title,
    author: presentation.author,
    format: normalizeString(item.format) || "txt",
    summary: normalizeString(item.short_summary),
    previewExcerpt: normalizeExcerpt(item.preview_excerpt),
    reason,
    matchReasons: normalizeStringArray(item.match_reasons),
    caveats: normalizeStringArray(item.caveats),
    tags,
    styleTags: normalizeStringArray(item.style_tags),
    coverUrl: normalizeString(item.cover_url) || undefined,
    mode: normalizeString(item.mode) || "similar",
    score: typeof item.score === "number" ? item.score : Number(item.score ?? 0),
    readUrl: normalizeString(item.read_url) || undefined,
    previewUrl: normalizeString(item.preview_url) || undefined,
    allowedActions: normalizeStringArray(item.actions),
    isSaved: false,
    searchContext,
    recommendationContext,
    requestMode,
    explanationMode: normalizeString(item.explanation_mode) || "profile_explanation",
    matchReasonContext:
      normalizeString(item.match_reason_context) === "query" ? "query" : "profile",
  };
}

function buildBookContent(raw: RawBookContentResponse): BookContent {
  const presentation = normalizeRecommendationPresentation(raw.title, raw.author);

  return {
    id: normalizeString(raw.book_id),
    title: presentation.title,
    author: presentation.author,
    format: normalizeString(raw.format) || "txt",
    summary: normalizeString(raw.summary),
    content: normalizeString(raw.content),
    contentLength:
      typeof raw.content_length === "number"
        ? raw.content_length
        : normalizeString(raw.content).length,
    coverUrl: normalizeString(raw.cover_url) || undefined,
    genres: normalizeStringArray(raw.genres),
    moods: normalizeStringArray(raw.moods),
    previewExcerpt: normalizeExcerpt(raw.preview_excerpt),
    readUrl: normalizeString(raw.read_url) || undefined,
  };
}

function buildBookPreview(raw: RawBookPreviewResponse): BookPreview {
  const presentation = normalizeRecommendationPresentation(raw.title, raw.author);

  return {
    id: normalizeString(raw.book_id),
    title: presentation.title,
    author: presentation.author,
    format: normalizeString(raw.format) || "txt",
    summary: normalizeString(raw.short_summary),
    previewExcerpt: normalizeExcerpt(raw.preview_excerpt),
    coverUrl: normalizeString(raw.cover_url) || undefined,
    genres: normalizeStringArray(raw.genres),
    moods: normalizeStringArray(raw.moods),
    readUrl: normalizeString(raw.read_url) || undefined,
  };
}

export function adaptProfileResponse(raw: RawProfileResponse): ReaderProfile {
  return buildProfile(raw, normalizeString(raw.warning) || undefined);
}

export function isOnboardingComplete(profile: ReaderProfile | RawProfileResponse | null | undefined) {
  if (!profile) {
    return false;
  }

  if ("hasCompletedOnboarding" in profile) {
    return Boolean(profile.hasCompletedOnboarding);
  }

  return hasMeaningfulValues(profileOnboardingRecord(profile));
}

export function createProfileQueryKey(sessionId?: string) {
  return ["product", "profile", sessionId ?? "global"] as const;
}

export function createFeedQueryKey(params: FeedQueryParams) {
  return [
    "product",
    "feed",
    params.searchContext ?? params.recommendationContext ?? "profile",
    params.recommendationContext ?? "profile",
    params.mode ?? "similar",
    params.limit ?? 8,
    params.sessionId ?? "global",
    params.seedBookId ?? "",
    params.query ?? "",
    (params.excludeBookIds ?? []).join(","),
    params.cursor ?? "",
    params.offset ?? 0,
  ] as const;
}

export async function getReaderProfile(sessionId?: string) {
  const query = new URLSearchParams();

  if (sessionId) {
    query.set("session_id", sessionId);
  }

  const suffix = query.toString();
  const response = await apiFetch<RawProfileResponse>(
    `/api/v1/profile${suffix ? `?${suffix}` : ""}`,
    undefined,
    { auth: true },
  );

  return adaptProfileResponse(response);
}

export async function submitOnboarding(payload: OnboardingPayload): Promise<OnboardingResult> {
  const response = await apiFetch<RawOnboardingResponse>(
    "/api/v1/onboarding",
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
    { auth: true },
  );

  return {
    status: normalizeString(response.status) || "ok",
    warning: normalizeString(response.warning) || undefined,
    profile: response.taste_profile ? buildProfile(response.taste_profile, response.warning) : null,
  };
}

export async function getRecommendationFeed(params: FeedQueryParams = {}): Promise<RecommendationFeed> {
  const query = new URLSearchParams();
  const excludeBookIds = params.excludeBookIds ?? [];

  if (params.mode) {
    query.set("mode", params.mode);
  }
  if (params.recommendationContext) {
    query.set("recommendation_context", params.recommendationContext);
  }
  if (params.searchContext) {
    query.set("search_context", params.searchContext);
  }
  if (typeof params.limit === "number") {
    query.set("limit", String(params.limit));
  }
  if (params.sessionId) {
    query.set("session_id", params.sessionId);
  }
  if (params.seedBookId) {
    query.set("seed_book_id", params.seedBookId);
  }
  if (params.query) {
    query.set("query", params.query);
  }
  if (excludeBookIds.length > 0) {
    query.set("exclude_book_ids", excludeBookIds.join(","));
  }
  if (params.cursor) {
    query.set("cursor", params.cursor);
  }
  if (typeof params.offset === "number") {
    query.set("offset", String(params.offset));
  }

  const response = await apiFetch<RawRecommendationFeedResponse>(
    `/api/v1/recommendations/feed?${query.toString()}`,
    undefined,
    { auth: true },
  );

  return {
    userId: normalizeString(response.user_id),
    mode: normalizeString(response.mode) || params.mode || "similar",
    searchContext: normalizeSearchContext(
      response.search_context,
      params.searchContext ?? params.recommendationContext ?? "profile",
    ),
    recommendationContext:
      normalizeString(response.recommendation_context) === "query"
        ? "query"
        : (params.recommendationContext ?? "profile"),
    requestMode: normalizeRequestMode(
      response.request_mode,
      normalizeSearchContext(
        response.search_context,
        params.searchContext ?? params.recommendationContext ?? "profile",
      ),
      normalizeString(response.query),
    ),
    query: normalizeString(response.query),
    cursor: normalizeString(response.cursor) || null,
    offset: typeof response.offset === "number" ? response.offset : 0,
    count: typeof response.count === "number" ? response.count : (response.items ?? []).length,
    cached: Boolean(response.cached),
    profile: response.profile ? buildProfile(response.profile) : null,
    items: (response.items ?? []).map((item) => buildRecommendation(item)),
  };
}

export async function getRecommendationExplanation(
  params: ExplainQueryParams,
): Promise<RecommendationExplanation> {
  const query = new URLSearchParams({ book_id: params.bookId });
  const excludeBookIds = params.excludeBookIds ?? [];

  if (params.mode) {
    query.set("mode", params.mode);
  }
  if (params.recommendationContext) {
    query.set("recommendation_context", params.recommendationContext);
  }
  if (params.searchContext) {
    query.set("search_context", params.searchContext);
  }
  if (params.sessionId) {
    query.set("session_id", params.sessionId);
  }
  if (params.seedBookId) {
    query.set("seed_book_id", params.seedBookId);
  }
  if (params.query) {
    query.set("query", params.query);
  }
  if (excludeBookIds.length > 0) {
    query.set("exclude_book_ids", excludeBookIds.join(","));
  }

  const response = await apiFetch<RawRecommendationExplainResponse>(
    `/api/v1/recommendations/explain?${query.toString()}`,
    undefined,
    { auth: true },
  );

  return {
    bookId: normalizeString(response.book_id) || params.bookId,
    source: normalizeString(response.source) || "fresh_feed",
    item: response.item ? buildRecommendation(response.item) : null,
    searchContext: normalizeSearchContext(
      response.search_context,
      params.searchContext ?? params.recommendationContext ?? "profile",
    ),
    recommendationContext:
      normalizeString(response.recommendation_context) === "query"
        ? "query"
        : (params.recommendationContext ?? "profile"),
    requestMode: normalizeRequestMode(
      response.request_mode,
      normalizeSearchContext(
        response.search_context,
        params.searchContext ?? params.recommendationContext ?? "profile",
      ),
      params.query ?? "",
    ),
  };
}

export async function getBookContent(bookId: string): Promise<BookContent> {
  const response = await apiFetch<RawBookContentResponse>(
    `/api/v1/books/${encodeURIComponent(bookId)}/content`,
    undefined,
    { auth: true },
  );

  return buildBookContent(response);
}

export async function getBookPreview(bookId: string): Promise<BookPreview> {
  const response = await apiFetch<RawBookPreviewResponse>(
    `/api/v1/books/${encodeURIComponent(bookId)}/preview`,
    undefined,
    { auth: true },
  );

  return buildBookPreview(response);
}

export async function postInteraction(input: InteractionInput): Promise<InteractionResult> {
  const response = await apiFetch<RawInteractionResponse>(
    "/api/v1/interactions",
    {
      method: "POST",
      body: JSON.stringify({
        ...input,
        source: input.source ?? "web_feed",
      }),
    },
    { auth: true },
  );

  return {
    status: normalizeString(response.status) || "ok",
    warning: normalizeString(response.warning) || undefined,
    action: input.action,
    bookId: input.book_id,
    profile: response.remote?.profile ? buildProfile(response.remote.profile) : null,
  };
}

export async function resolvePostAuthPath() {
  return "/feed";
}
