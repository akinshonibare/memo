import * as SecureStore from "expo-secure-store";

// The API base URL and the session token (ADR 0019). The token is issued by the server
// after Sign in with Apple and lives only in the device's Keychain. The base URL has a
// production default; the override exists for pointing a build at a local API.
export const DEFAULT_BASE_URL =
  process.env.EXPO_PUBLIC_API_URL ?? "https://api-production-fd83.up.railway.app";

const KEY_BASE_URL = "memo.baseUrl";
const KEY_TOKEN = "memo.session";

export type Config = { baseUrl: string; token: string | null };

const trimSlash = (url: string) => url.replace(/\/+$/, "");

export async function loadConfig(): Promise<Config> {
  const [baseUrl, token] = await Promise.all([
    SecureStore.getItemAsync(KEY_BASE_URL),
    SecureStore.getItemAsync(KEY_TOKEN),
  ]);
  return { baseUrl: trimSlash(baseUrl || DEFAULT_BASE_URL), token: token || null };
}

export async function saveBaseUrl(baseUrl: string): Promise<void> {
  await SecureStore.setItemAsync(KEY_BASE_URL, trimSlash(baseUrl.trim()));
}

export async function saveSession(token: string): Promise<void> {
  await SecureStore.setItemAsync(KEY_TOKEN, token);
}

export async function clearSession(): Promise<void> {
  await SecureStore.deleteItemAsync(KEY_TOKEN);
}
