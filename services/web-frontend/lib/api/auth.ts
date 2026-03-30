import { apiFetch } from "@/lib/api/client";
import type {
  AuthResponse,
  LoginInput,
  MeResponse,
  RefreshInput,
  RegisterInput,
} from "@/lib/api/types";

export function register(input: RegisterInput) {
  return apiFetch<AuthResponse>("/api/v1/auth/register", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function login(input: LoginInput) {
  return apiFetch<AuthResponse>("/api/v1/auth/login", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function refresh(input: RefreshInput) {
  return apiFetch<AuthResponse>("/api/v1/auth/refresh", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function getCurrentUser() {
  return apiFetch<MeResponse>("/api/v1/me", undefined, { auth: true });
}
