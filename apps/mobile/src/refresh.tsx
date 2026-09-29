import { useCallback, useState } from "react";
import { RefreshControl } from "react-native";
import { useTheme } from "./theme";

// Pull down to refresh, on every screen that shows a list. `load` is the screen's own
// fetch; the spinner stays until it settles, whether it worked or not.
export function usePullToRefresh(load: () => Promise<unknown>) {
  const { colors } = useTheme();
  const [refreshing, setRefreshing] = useState(false);
  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    try {
      await load();
    } finally {
      setRefreshing(false);
    }
  }, [load]);
  return <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.muted} />;
}
