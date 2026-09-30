import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { get, post } from "./api";
import type { User } from "./types";

interface AuthCtx {
  user: User | null;
  ready: boolean;
  refresh: () => Promise<void>;
  login: (email: string, password: string) => Promise<User>;
  register: (email: string, password: string, display_name: string) => Promise<User>;
  logout: () => Promise<void>;
  setUser: (u: User | null) => void;
}

const Ctx = createContext<AuthCtx | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const r = await get<{ user: User | null }>("/api/auth/me");
      setUser(r.user);
    } catch {
      setUser(null);
    } finally {
      setReady(true);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const login = async (email: string, password: string) => {
    const r = await post<{ user: User }>("/api/auth/login", { email, password });
    setUser(r.user);
    return r.user;
  };
  const register = async (email: string, password: string, display_name: string) => {
    const r = await post<{ user: User }>("/api/auth/register", { email, password, display_name });
    setUser(r.user);
    return r.user;
  };
  const logout = async () => {
    try {
      await post("/api/auth/logout");
    } finally {
      setUser(null);
    }
  };
  return <Ctx.Provider value={{ user, ready, refresh, login, register, logout, setUser }}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error("AuthProvider missing");
  return c;
}
