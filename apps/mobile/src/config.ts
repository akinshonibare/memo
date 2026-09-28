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
  if (!baseUrl || !token) return null;
  return { baseUrl: baseUrl.replace(/\/+$/, ""), token };
}

export async function saveConfig(config: Config): Promise<void> {
  await SecureStore.setItemAsync(KEY_BASE_URL, config.baseUrl.trim());
  await SecureStore.setItemAsync(KEY_TOKEN, config.token.trim());
}
