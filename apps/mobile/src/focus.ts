import { useFocusEffect } from "@react-navigation/native";
import { useCallback, useRef } from "react";

// Screens stay mounted underneath the one on top of them (ADR 0033), so coming back
// to one can find it out of date: a mention answered on the note above, a todo ticked.
// This runs `load` each time the screen comes back into view - not on its first
// appearance, which the screen's own mount effect already covers.
export function useRefetchOnFocus(load: () => unknown) {
  const first = useRef(true);
  useFocusEffect(
    useCallback(() => {
      if (first.current) {
        first.current = false;
        return;
      }
      load();
    }, [load]),
  );
}
