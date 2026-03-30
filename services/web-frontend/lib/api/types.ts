export type User = {
  id: string;
  email: string;
  display_name: string;
  created_at: number;
};

export type AuthTokens = {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
};

export type AuthResponse = {
  user: User;
  tokens: AuthTokens;
};

export type MeResponse = {
  user: User;
};

export type LoginInput = {
  email: string;
  password: string;
};

export type RegisterInput = {
  email: string;
  password: string;
  display_name?: string;
};

export type RefreshInput = {
  refresh_token: string;
};

export type PreferenceScore = {
  label: string;
  score: number;
};

export type ReaderPriorities = {
  mood: PreferenceScore[];
  tone: PreferenceScore[];
  style: PreferenceScore[];
  atmosphere: PreferenceScore[];
  plot: PreferenceScore[];
  characters: PreferenceScore[];
};

export type RawTasteProfile = {
  user_id?: string;
  onboarding?: Record<string, unknown> | null;
  preferred_genres?: string[] | null;
  preferred_authors?: string[] | null;
  favorite_book_ids?: string[] | null;
  saved_books?: string[] | null;
  priorities?: Partial<Record<keyof ReaderPriorities, Array<[string, number] | string>>> | null;
  interaction_counters?: Record<string, number> | null;
  [key: string]: unknown;
};

export type RawProfileResponse = {
  user_id?: string;
  onboarding?: Record<string, unknown> | null;
  taste_profile?: RawTasteProfile | null;
  warning?: string;
};

export type ReaderProfile = {
  userId: string;
  onboarding: Record<string, unknown>;
  preferredGenres: string[];
  preferredAuthors: string[];
  favoriteBooks: string[];
  savedBookIds: string[];
  readingGoals: string[];
  moods: string[];
  tones: string[];
  styles: string[];
  atmosphere: string[];
  plotInterests: string[];
  priorities: ReaderPriorities;
  interactionCounters: Record<string, number>;
  hasCompletedOnboarding: boolean;
  warning?: string;
};

export type OnboardingPayload = {
  favorite_genres: string[];
  favorite_authors: string[];
  favorite_books: string[];
  reading_goals: string[];
  favorite_moods: string[];
  favorite_tone: string[];
  favorite_style: string[];
  favorite_atmosphere: string[];
  favorite_plot: string[];
};

export type RawOnboardingResponse = {
  status: string;
  warning?: string;
  onboarding?: {
    user_id: string;
    payload: Record<string, unknown>;
    completed: boolean;
    updated_at: number;
  };
  taste_profile?: RawTasteProfile;
};

export type OnboardingResult = {
  status: string;
  warning?: string;
  profile: ReaderProfile | null;
};

export type RawRecommendationItem = {
  book_id: string;
  title?: string;
  author?: string;
  format?: string;
  short_summary?: string;
  preview_excerpt?: string;
  why_for_you?: string;
  explanation?: string;
  genres?: string[] | null;
  moods?: string[] | null;
  style_tags?: string[] | null;
  match_reasons?: string[] | null;
  caveats?: string[] | null;
  read_url?: string;
  preview_url?: string;
  cover_url?: string;
  mode?: string;
  score?: number;
  actions?: string[] | null;
  search_context?: string;
  recommendation_context?: string;
  request_mode?: string;
  explanation_mode?: string;
  match_reason_context?: string;
};

export type RawRecommendationFeedResponse = {
  user_id?: string;
  mode?: string;
  search_context?: string;
  recommendation_context?: string;
  request_mode?: string;
  query?: string;
  cursor?: string | null;
  offset?: number;
  count?: number;
  cached?: boolean;
  profile?: RawTasteProfile | null;
  items?: RawRecommendationItem[] | null;
};

export type Recommendation = {
  id: string;
  title: string;
  author: string;
  format: string;
  summary: string;
  previewExcerpt: string;
  reason: string;
  matchReasons: string[];
  caveats: string[];
  tags: string[];
  styleTags: string[];
  coverUrl?: string;
  mode: string;
  score: number;
  readUrl?: string;
  previewUrl?: string;
  allowedActions: string[];
  isSaved: boolean;
  searchContext: "profile" | "query";
  recommendationContext: "profile" | "query";
  requestMode: "profile_only" | "query_only" | "profile_with_query";
  explanationMode: string;
  matchReasonContext: "profile" | "query";
};

export type RecommendationFeed = {
  userId: string;
  mode: string;
  searchContext: "profile" | "query";
  recommendationContext: "profile" | "query";
  requestMode: "profile_only" | "query_only" | "profile_with_query";
  query: string;
  cursor: string | null;
  offset: number;
  count: number;
  cached: boolean;
  profile: ReaderProfile | null;
  items: Recommendation[];
};

export type RawRecommendationExplainResponse = {
  book_id: string;
  item?: RawRecommendationItem | null;
  source?: string;
  search_context?: string;
  recommendation_context?: string;
  request_mode?: string;
};

export type RecommendationExplanation = {
  bookId: string;
  source: string;
  item: Recommendation | null;
  searchContext: "profile" | "query";
  recommendationContext: "profile" | "query";
  requestMode: "profile_only" | "query_only" | "profile_with_query";
};

export type InteractionAction = "like" | "dislike" | "skip" | "save";

export type InteractionInput = {
  book_id: string;
  action: InteractionAction;
  session_id?: string;
  source?: string;
  metadata?: Record<string, unknown>;
};

export type RawInteractionResponse = {
  status: string;
  warning?: string;
  interaction?: {
    id: string;
    user_id: string;
    book_id: string;
    action: string;
    session_id?: string;
    source?: string;
    metadata?: Record<string, unknown>;
    created_at: number;
  };
  remote?: {
    profile?: RawTasteProfile;
  };
};

export type InteractionResult = {
  status: string;
  warning?: string;
  action: InteractionAction;
  bookId: string;
  profile: ReaderProfile | null;
};

export type RawBookContentResponse = {
  book_id: string;
  title?: string;
  author?: string;
  format?: string;
  summary?: string;
  content?: string;
  content_length?: number;
  cover_url?: string;
  genres?: string[] | null;
  moods?: string[] | null;
  preview_excerpt?: string;
  read_url?: string;
};

export type RawBookPreviewResponse = {
  book_id: string;
  title?: string;
  author?: string;
  format?: string;
  short_summary?: string;
  preview_excerpt?: string;
  cover_url?: string;
  genres?: string[] | null;
  moods?: string[] | null;
  read_url?: string;
};

export type BookContent = {
  id: string;
  title: string;
  author: string;
  format: string;
  summary: string;
  content: string;
  contentLength: number;
  coverUrl?: string;
  genres: string[];
  moods: string[];
  previewExcerpt: string;
  readUrl?: string;
};

export type BookPreview = {
  id: string;
  title: string;
  author: string;
  format: string;
  summary: string;
  previewExcerpt: string;
  coverUrl?: string;
  genres: string[];
  moods: string[];
  readUrl?: string;
};
