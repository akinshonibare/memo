import { useEffect, useState } from "react";
import { Keyboard, type KeyboardEvent, Platform } from "react-native";

// KeyboardAvoidingView misjudges its offset inside a pageSheet modal, so the sheets
// pad themselves by the keyboard's real height instead.
export function useKeyboardHeight(): number {
  const [height, setHeight] = useState(0);
  useEffect(() => {
    const showEvent = Platform.OS === "ios" ? "keyboardWillChangeFrame" : "keyboardDidShow";
    const hideEvent = Platform.OS === "ios" ? "keyboardWillHide" : "keyboardDidHide";
    const onShow = (e: KeyboardEvent) => setHeight(Math.max(0, e.endCoordinates.height));
    const onHide = () => setHeight(0);
    const a = Keyboard.addListener(showEvent, onShow);
    const b = Keyboard.addListener(hideEvent, onHide);
    return () => {
      a.remove();
      b.remove();
    };
  }, []);
  return height;
}
