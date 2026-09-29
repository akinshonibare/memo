import * as AppleAuthentication from "expo-apple-authentication";
import { useState } from "react";
import { Platform, Pressable, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Api, ApiError } from "../api";
import { Wordmark } from "../motion";
import { type Config, saveSession } from "../config";
import { useUi } from "../ui";
import { makeStyles, useTheme } from "../theme";

// One tap. Apple proves who you are; the server hands back a session that lives in the
// Keychain (ADR 0019). Nothing to copy from anywhere.
export function LoginScreen({
  config,
  onSignedIn,
  onChangeServer,
}: {
  config: Config;
  onSignedIn: (token: string) => void;
  onChangeServer: () => void;
}) {
  const ui = useUi();
  const styles = useStyles();
  const { scheme } = useTheme();
  const insets = useSafeAreaInsets();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const signIn = async () => {
    setError(null);
    setBusy(true);
    try {
      const credential = await AppleAuthentication.signInAsync({
        requestedScopes: [
          AppleAuthentication.AppleAuthenticationScope.FULL_NAME,
          AppleAuthentication.AppleAuthenticationScope.EMAIL,
        ],
      });
      if (!credential.identityToken) throw new Error("Apple returned no identity token.");
      const name = [credential.fullName?.givenName, credential.fullName?.familyName]
        .filter(Boolean)
        .join(" ");
      const api = new Api({ ...config, token: null });
      const result = await api.loginWithApple({
        identity_token: credential.identityToken,
        full_name: name || undefined,
        device_name: Platform.OS === "ios" ? "iPhone" : Platform.OS,
      });
      await saveSession(result.token);
      try {
        await new Api({ ...config, token: result.token }).setTimezone(deviceTimezone());
      } catch {
        // Not worth failing sign-in over; Settings can set it later.
      }
      onSignedIn(result.token);
    } catch (e) {
      const code = (e as { code?: string }).code;
      if (code === "ERR_REQUEST_CANCELED") return;
      setError(e instanceof ApiError ? `Server said: ${e.message}` : String(e));
    } finally {
      setBusy(false);
    }
  };

  const host = config.baseUrl.replace(/^https?:\/\//, "");
  return (
    <View style={[ui.screen, styles.wrap, { paddingBottom: Math.max(insets.bottom, 24) }]}>
      <View style={{ flex: 1 }} />
      <Wordmark live={busy} size={60} style={{ alignSelf: "center" }} />
      <Text style={styles.tagline}>Say it or type it. It remembers.</Text>
      <View style={{ flex: 1 }} />
      {error && <Text style={[ui.danger, styles.error]}>{error}</Text>}
      <AppleAuthentication.AppleAuthenticationButton
        buttonType={AppleAuthentication.AppleAuthenticationButtonType.SIGN_IN}
        // Apple's rule: the white button on dark backgrounds, the black one on light.
        buttonStyle={
          scheme === "dark"
            ? AppleAuthentication.AppleAuthenticationButtonStyle.WHITE
            : AppleAuthentication.AppleAuthenticationButtonStyle.BLACK
        }
        cornerRadius={28}
        style={[styles.apple, busy && { opacity: 0.5 }]}
        onPress={busy ? () => {} : signIn}
      />
      {/* Where the app points is a developer concern; production builds don't show it. */}
      {__DEV__ ? (
        <Pressable onPress={onChangeServer} hitSlop={8} style={styles.server}>
          <Text style={styles.serverText}>{host}</Text>
        </Pressable>
      ) : (
        <View style={styles.server} />
      )}
    </View>
  );
}

function deviceTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

const useStyles = makeStyles((colors) => ({
  wrap: { paddingHorizontal: 24, alignItems: "stretch" },
  tagline: { color: colors.muted, fontSize: 16, textAlign: "center", marginTop: 4 },
  error: { textAlign: "center", marginBottom: 16 },
  apple: { height: 56, width: "100%" },
  server: { alignSelf: "center", marginTop: 20, padding: 8 },
  serverText: { color: colors.faint, fontSize: 13 },
}));
