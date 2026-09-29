import * as Haptics from "expo-haptics";

// A small vocabulary, used the same way everywhere, so each feel means one thing:
// tap - a control responded; press - something started (record, voice);
// done - something was saved or ticked off; pick - a choice between options;
// warn - a destructive confirm is about to appear. Failures are ignored on purpose:
// a device without a Taptic Engine should never surface an error for a buzz.
const quietly = (p: Promise<void>) => void p.catch(() => {});

export const haptic = {
  tap: () => quietly(Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light)),
  press: () => quietly(Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium)),
  done: () => quietly(Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success)),
  pick: () => quietly(Haptics.selectionAsync()),
  warn: () => quietly(Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning)),
};
