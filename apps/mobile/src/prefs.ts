import * as SecureStore from "expo-secure-store";
import { useEffect, useState } from "react";

// A small setting kept on this phone (the calendar, reminders), readable from any
// screen and live-updated across them. Account settings live on the server (`/v1/me`);
// these are about this device and what it has permission to do.
export function devicePref<T>(key: string, fallback: T) {
  let value: T | undefined;
  let loading: Promise<T> | null = null;
  const listeners = new Set<(v: T) => void>();

  const get = (): Promise<T> => {
    if (value !== undefined) return Promise.resolve(value);
    loading ??= SecureStore.getItemAsync(key)
      .then((raw) => {
        try {
          value = raw ? ({ ...(fallback as object), ...JSON.parse(raw) } as T) : fallback;
        } catch {
          value = fallback;
        }
        return value;
      })
      .catch(() => (value = fallback));
    return loading;
  };

  const set = async (next: T) => {
    value = next;
    for (const l of listeners) l(next);
    await SecureStore.setItemAsync(key, JSON.stringify(next)).catch(() => {});
  };

  const use = (): T => {
    const [state, setState] = useState<T>(value ?? fallback);
    useEffect(() => {
      listeners.add(setState);
      get().then(setState);
      return () => {
        listeners.delete(setState);
      };
    }, []);
    return state;
  };

  return { get, set, use };
}
