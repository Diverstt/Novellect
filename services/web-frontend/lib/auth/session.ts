import type { AuthResponse } from "@/lib/api/types";

const STORAGE_KEY = "novellect.auth.session";
const SESSION_EVENT = "novellect:session-changed";
const FEED_SESSION_KEY = "novellect.feed.session";

export type StoredSession = AuthResponse & {
  saved_at: number;
};

function canUseStorage() {
  return typeof window !== "undefined" && typeof window.localStorage !== "undefined";
}

export function getSession(): StoredSession | null {
  if (!canUseStorage()) {
    return null;
  }

  const raw = window.localStorage.getItem(STORAGE_KEY);
  if (!raw) {
    return null;
  }

  try {
    return JSON.parse(raw) as StoredSession;
  } catch {
    window.localStorage.removeItem(STORAGE_KEY);
    return null;
  }
}

export function saveSession(payload: AuthResponse) {
  if (!canUseStorage()) {
    return;
  }

  const previousUserId = getSession()?.user.id;
  if (previousUserId && previousUserId !== payload.user.id) {
    window.localStorage.removeItem(FEED_SESSION_KEY);
  }

  const value: StoredSession = {
    ...payload,
    saved_at: Date.now(),
  };
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(value));
  window.dispatchEvent(new Event(SESSION_EVENT));
}

export function clearSession() {
  if (!canUseStorage()) {
    return;
  }
  window.localStorage.removeItem(STORAGE_KEY);
  window.localStorage.removeItem(FEED_SESSION_KEY);
  window.dispatchEvent(new Event(SESSION_EVENT));
}

export function hasSession() {
  return Boolean(getSession()?.tokens.access_token);
}

export function getAccessToken() {
  return getSession()?.tokens.access_token ?? null;
}

export function getRefreshToken() {
  return getSession()?.tokens.refresh_token ?? null;
}

export function getSessionEventName() {
  return SESSION_EVENT;
}

export function getFeedSessionId() {
  if (!canUseStorage()) {
    return "novellect-web-session";
  }

  const existing = window.localStorage.getItem(FEED_SESSION_KEY);
  if (existing) {
    return existing;
  }

  const generated =
    typeof window.crypto !== "undefined" && "randomUUID" in window.crypto
      ? `feed-${window.crypto.randomUUID()}`
      : `feed-${Date.now()}`;

  window.localStorage.setItem(FEED_SESSION_KEY, generated);
  return generated;
}
