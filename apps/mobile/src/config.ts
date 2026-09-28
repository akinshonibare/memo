import * as SecureStore from "expo-secure-store";

// The API base URL and the static bearer token (ADR 0018). Both live in the device's
// secure storage; nothing about the account is derivable from the app bundle.
const KEY_BASE_URL = "memo.baseUrl";
const KEY_TOKEN = "memo.token";

export type Config = { baseUrl: string; token: string };

export async function loadConfig(): Promise<Config | null> {
  const [baseUrl, token] = await Promise.all([
    SecureStore.getItemAsync(KEY_BASE_URL),
    SecureStore.getItemAsync(KEY_TOKEN),
  ]);
  if (baseUrl && token) return { baseUrl: baseUrl.replace(/\/+$/, ""), token };
  // Development convenience: `EXPO_PUBLIC_API_URL=... EXPO_PUBLIC_API_TOKEN=... npx expo
  // start` pre-configures a fresh install. Inlined at bundle time, so never set these
  // for a production build.
  const envUrl = process.env.EXPO_PUBLIC_API_URL;
  const envToken = process.env.EXPO_PUBLIC_API_TOKEN;
  if (envUrl && envToken) return { baseUrl: envUrl.replace(/\/+$/, ""), token: envToken };
  return null;
}

export async function saveConfig(config: Config): Promise<void> {
  await SecureStore.setItemAsync(KEY_BASE_URL, config.baseUrl.trim());
  await SecureStore.setItemAsync(KEY_TOKEN, config.token.trim());
}
