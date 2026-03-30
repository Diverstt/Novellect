import type { AuthResponse } from "@/lib/api/types";
import { clearSession, getAccessToken, getRefreshToken, saveSession } from "@/lib/auth/session";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "") ?? "";

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export function localizeApiErrorMessage(message: string) {
  const normalized = message.trim().toLowerCase();

  if (normalized === "user already exists") {
    return "Пользователь с таким email уже существует.";
  }

  if (normalized === "email and password>=6 required") {
    return "Укажи email и пароль длиной не меньше 6 символов.";
  }

  if (normalized === "missing bearer token") {
    return "Сессия не найдена. Войди заново.";
  }

  if (normalized === "request failed") {
    return "Не удалось выполнить запрос.";
  }

  if (normalized.startsWith("recommendation service returned ")) {
    return "Сервис рекомендаций временно недоступен.";
  }

  return message;
}

type ApiFetchOptions = {
  auth?: boolean;
  retryOnUnauthorized?: boolean;
};

type ApiErrorPayload = {
  error?: string;
};

async function parseResponse<T>(response: Response): Promise<T> {
  const text = await response.text();
  const payload = text ? (JSON.parse(text) as T | ApiErrorPayload) : null;

  if (!response.ok) {
    const message =
      payload && typeof payload === "object" && "error" in payload && payload.error
        ? payload.error
        : "Не удалось выполнить запрос.";
    throw new ApiError(message, response.status);
  }

  return payload as T;
}

async function refreshAccessToken(): Promise<boolean> {
  const refreshToken = getRefreshToken();
  if (!refreshToken) {
    clearSession();
    return false;
  }

  const response = await fetch(`${API_BASE_URL}/api/v1/auth/refresh`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ refresh_token: refreshToken }),
    cache: "no-store",
  });

  if (!response.ok) {
    clearSession();
    return false;
  }

  const payload = await parseResponse<AuthResponse>(response);
  saveSession(payload);
  return true;
}

export async function apiFetch<T>(
  path: string,
  init: RequestInit = {},
  options: ApiFetchOptions = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Content-Type", "application/json");

  if (options.auth) {
    const accessToken = getAccessToken();
    if (accessToken) {
      headers.set("Authorization", `Bearer ${accessToken}`);
    }
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers,
    cache: "no-store",
  });

  if (response.status === 401 && options.auth && options.retryOnUnauthorized !== false) {
    const refreshed = await refreshAccessToken();
    if (refreshed) {
      return apiFetch<T>(path, init, { ...options, retryOnUnauthorized: false });
    }
    clearSession();
  }

  return parseResponse<T>(response);
}
