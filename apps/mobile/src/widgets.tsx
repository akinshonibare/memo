import { HStack, Image, Spacer, Text, VStack } from "@expo/ui/swift-ui";
import {
  containerBackground,
  font,
  foregroundStyle,
  frame,
  lineLimit,
  padding,
  widgetURL,
} from "@expo/ui/swift-ui/modifiers";
import { createWidget, type WidgetEnvironment } from "expo-widgets";
import { Platform } from "react-native";

// Home and Lock Screen widgets (ADR 0027). Each render function is marked 'widget':
// expo-widgets serialises it into the widget extension's own JS runtime, so it may
// use only its props and environment - no imports from the app, no closures. Colours
// are therefore written out here rather than read from ui.ts (they match docs/brand.md).

export type TodayLine = { time: string | null; title: string; kind: "event" | "todo" };
export type TodayProps = { heading: string; lines: TodayLine[]; more: number };

const Today = (props: TodayProps, env: WidgetEnvironment) => {
  "widget";
  const cream = "#F1ECDF";
  const muted = "#9FAEA4";
  const amber = "#E2A04A";
  const bg = "#0B241A";
  if (env.widgetFamily === "accessoryRectangular") {
    const next = props.lines[0];
    return (
      <VStack alignment="leading" spacing={1} modifiers={[widgetURL("memo://planner")]}>
        <Text modifiers={[font({ size: 13, weight: "semibold" })]}>Today</Text>
        <Text modifiers={[font({ size: 13 })]}>
          {next ? `${next.time ? `${next.time} ` : ""}${next.title}` : "Nothing planned"}
        </Text>
      </VStack>
    );
  }
  const room = env.widgetFamily === "systemMedium" ? 4 : 3;
  const shown = props.lines.slice(0, room);
  return (
    <VStack
      alignment="leading"
      spacing={6}
      modifiers={[
        frame({ maxWidth: 9999, maxHeight: 9999, alignment: "topLeading" }),
        padding({ all: 2 }),
        containerBackground(bg, "widget"),
        widgetURL("memo://planner"),
      ]}
    >
      <Text modifiers={[font({ size: 17, design: "serif" }), foregroundStyle(cream)]}>{props.heading}</Text>
      {shown.length === 0 ? (
        <Text modifiers={[font({ size: 13 }), foregroundStyle(muted)]}>Nothing planned. A quiet day.</Text>
      ) : (
        shown.map((line, i) => (
          <HStack key={i} spacing={6}>
            <Image
              systemName={line.kind === "event" ? "clock" : "circle"}
              size={11}
              color={line.kind === "event" ? amber : muted}
            />
            <Text modifiers={[font({ size: 13 }), foregroundStyle(cream)]}>
              {line.time ? `${line.time}  ${line.title}` : line.title}
            </Text>
          </HStack>
        ))
      )}
      <Spacer />
      {props.more > 0 && (
        <Text modifiers={[font({ size: 11 }), foregroundStyle(muted)]}>{`+${props.more} more`}</Text>
      )}
    </VStack>
  );
};

const Record = (_props: object, env: WidgetEnvironment) => {
  "widget";
  if (env.widgetFamily === "accessoryCircular") {
    return <Image systemName="mic.fill" size={22} modifiers={[widgetURL("memo://record")]} />;
  }
  return (
    <VStack
      spacing={8}
      modifiers={[containerBackground("#0B241A", "widget"), widgetURL("memo://record")]}
    >
      <Image systemName="mic.circle.fill" size={46} color="#E2A04A" />
      <Text modifiers={[font({ size: 15, design: "serif" }), foregroundStyle("#F1ECDF")]}>Record</Text>
    </VStack>
  );
};

// What the On this day widget shows: a note from this day in another year, else the
// idea Today resurfaced, else a word that there's nothing yet (ADR 0057).
export type OnThisDayProps = {
  label: string; // "A year ago today", "An idea worth another look"
  text: string;
  date: string | null; // the note's own date, so an old snapshot still reads true
  link: string; // memo://note/<id>, or memo://ideas
};

const OnThisDay = (props: OnThisDayProps, env: WidgetEnvironment) => {
  "widget";
  const cream = "#F1ECDF";
  const muted = "#9FAEA4";
  const amber = "#E2A04A";
  const bg = "#0B241A";
  const small = env.widgetFamily === "systemSmall";
  return (
    <VStack
      alignment="leading"
      spacing={6}
      modifiers={[
        frame({ maxWidth: 9999, maxHeight: 9999, alignment: "topLeading" }),
        padding({ all: 2 }),
        containerBackground(bg, "widget"),
        widgetURL(props.link),
      ]}
    >
      <HStack spacing={5}>
        <Image systemName="clock.arrow.circlepath" size={11} color={amber} />
        <Text modifiers={[font({ size: 11, weight: "semibold" }), foregroundStyle(muted), lineLimit(1)]}>
          {props.label}
        </Text>
      </HStack>
      <Text modifiers={[font({ size: small ? 14 : 16, design: "serif" }), foregroundStyle(cream), lineLimit(small ? 5 : 4)]}>
        {props.text}
      </Text>
      <Spacer />
      {props.date ? (
        <Text modifiers={[font({ size: 11 }), foregroundStyle(muted), lineLimit(1)]}>{props.date}</Text>
      ) : null}
    </VStack>
  );
};

export const TodayWidget = createWidget<TodayProps>("TodayWidget", Today);
export const OnThisDayWidget = createWidget<OnThisDayProps>("OnThisDayWidget", OnThisDay);
export const RecordWidget = createWidget("RecordWidget", Record);

// The widget shows what the app last saw: pushed whenever the morning view loads.
// iOS only, and never allowed to break the screen that calls it.
export function publishToday(props: TodayProps) {
  if (Platform.OS !== "ios") return;
  try {
    TodayWidget.updateSnapshot(props);
  } catch {
    // no widget extension in this build (Expo Go, simulator without the target)
  }
}

export function publishOnThisDay(props: OnThisDayProps) {
  if (Platform.OS !== "ios") return;
  try {
    OnThisDayWidget.updateSnapshot(props);
  } catch {
    // as above
  }
}

export function publishRecord() {
  if (Platform.OS !== "ios") return;
  try {
    RecordWidget.updateSnapshot({});
  } catch {
    // as above
  }
}
