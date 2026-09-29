import { Linking } from "react-native";

// Bible links (ADR 0042). memo stores references, never Scripture text: the NIV and
// most modern translations are licensed, so a reference opens in the user's Bible
// app instead. A bible.com link opens YouVersion when it's installed, and the web
// reader when it isn't.

export type Translation = "NIV" | "ESV" | "KJV" | "NKJV" | "NLT" | "CSB" | "NASB" | "MSG";

// bible.com's number and URL suffix for each, checked against bible.com (2026-09-29).
const VERSIONS: Record<Translation, { id: number; suffix: string; name: string }> = {
  NIV: { id: 111, suffix: "NIV", name: "New International Version" },
  ESV: { id: 59, suffix: "ESV", name: "English Standard Version" },
  KJV: { id: 1, suffix: "KJV", name: "King James Version" },
  NKJV: { id: 114, suffix: "NKJV", name: "New King James Version" },
  NLT: { id: 116, suffix: "NLT", name: "New Living Translation" },
  CSB: { id: 1713, suffix: "CSB", name: "Christian Standard Bible" },
  NASB: { id: 2692, suffix: "NASB2020", name: "New American Standard Bible" },
  MSG: { id: 97, suffix: "MSG", name: "The Message" },
};

export const TRANSLATIONS = Object.keys(VERSIONS) as Translation[];

export function translationName(t: Translation): string {
  return VERSIONS[t].name;
}

// Anything with a book (USFM code: ROM, 1CO) and a chapter; verses optional.
export type Reference = {
  book: string;
  chapter: number;
  verse?: number | null;
  end_chapter?: number;
  end_verse?: number | null;
};

// https://www.bible.com/bible/111/ROM.8.28-30.NIV. A range across chapters opens at
// its first verse: no single link can open two chapters.
export function bibleUrl(ref: Reference, translation: Translation = "NIV"): string {
  const v = VERSIONS[translation] ?? VERSIONS.NIV;
  let place = `${ref.book}.${ref.chapter}`;
  if (ref.verse) {
    place += `.${ref.verse}`;
    const sameChapter = !ref.end_chapter || ref.end_chapter === ref.chapter;
    if (sameChapter && ref.end_verse && ref.end_verse !== ref.verse) place += `-${ref.end_verse}`;
  }
  return `https://www.bible.com/bible/${v.id}/${place}.${v.suffix}`;
}

export function openInBible(ref: Reference, translation: Translation = "NIV") {
  void Linking.openURL(bibleUrl(ref, translation)).catch(() => {});
}

export function asTranslation(value: string | null | undefined): Translation {
  return value && value in VERSIONS ? (value as Translation) : "NIV";
}
