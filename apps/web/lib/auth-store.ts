"use client";
import { create } from "zustand";

// ponytail: tokens live in localStorage, which is XSS-exposed (any script
// running on this origin can read them). The real fix is httpOnly cookies
// via a backend-for-frontend, which needs a server component/API-route layer
// this app doesn't have yet. Fine for an internal V1 tool; revisit before
// this is exposed to a wider audience.
const ACCESS_KEY = "lamoon_access";
const REFRESH_KEY = "lamoon_refresh";

type AuthState = {
  accessToken: string | null;
  refreshToken: string | null;
  role: string | null;
  permissions: string[];
  companyId: string | null;
  setTokens: (access: string, refresh: string) => void;
  setProfile: (role: string, permissions: string[], companyId: string) => void;
  clear: () => void;
};

function loadTokens() {
  if (typeof window === "undefined") return { accessToken: null, refreshToken: null };
  return {
    accessToken: localStorage.getItem(ACCESS_KEY),
    refreshToken: localStorage.getItem(REFRESH_KEY),
  };
}

export const useAuthStore = create<AuthState>((set) => ({
  ...loadTokens(),
  role: null,
  permissions: [],
  companyId: null,
  setTokens: (access, refresh) => {
    localStorage.setItem(ACCESS_KEY, access);
    localStorage.setItem(REFRESH_KEY, refresh);
    set({ accessToken: access, refreshToken: refresh });
  },
  setProfile: (role, permissions, companyId) => set({ role, permissions, companyId }),
  clear: () => {
    localStorage.removeItem(ACCESS_KEY);
    localStorage.removeItem(REFRESH_KEY);
    set({ accessToken: null, refreshToken: null, role: null, permissions: [], companyId: null });
  },
}));

export function hasPermission(permissions: string[], needed: string): boolean {
  return permissions.includes("*") || permissions.includes(needed);
}
