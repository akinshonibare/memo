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

// A development build pointed at a local API can be handed a session minted there,
// because Sign in with Apple needs an Apple ID the simulator doesn't have. Only in
// __DEV__: a release build compiles this away, and it never overrides a real session.
const DEV_SESSION = __DEV__ ? process.env.EXPO_PUBLIC_DEV_SESSION : undefined;

export async function loadConfig(): Promise<Config> {
  const [baseUrl, token] = await Promise.all([
    SecureStore.getItemAsync(KEY_BASE_URL),
    SecureStore.getItemAsync(KEY_TOKEN),
  ]);
  return { baseUrl: trimSlash(baseUrl || DEFAULT_BASE_URL), token: token || DEV_SESSION || null };
}

export async function saveBaseUrl(baseUrl: string): Promise<void> {
  await SecureStore.setItemAsync(KEY_BASE_URL, trimSlash(baseUrl.trim()));
}

export async function saveSession(token: string): Promise<void> {
  await SecureStore.setItemAsync(KEY_TOKEN, token);
}

// First-run onboarding is a per-device convenience, not account state.
const KEY_ONBOARDED = "memo.onboarded";

export async function loadOnboarded(): Promise<boolean> {
  return (await SecureStore.getItemAsync(KEY_ONBOARDED)) === "1";
}

export async function saveOnboarded(): Promise<void> {
  await SecureStore.setItemAsync(KEY_ONBOARDED, "1");
}

export async function clearSession(): Promise<void> {
  await SecureStore.deleteItemAsync(KEY_TOKEN);
}
