import * as Calendar from "expo-calendar";
import * as FileSystem from "expo-file-system/legacy";
import { useEffect, useRef } from "react";
import { AppState, Platform } from "react-native";
import type { Api, Derivation } from "./api";
import { devicePref } from "./prefs";
import { cancelNotifications, remindersPref, syncNotifications } from "./reminders";
import { heal, itemIdFromTag, type Mapping, planEvents, planReminders, tagFor } from "./syncPlan";

// memo on the phone (ADR 0034): two switches in Settings, no per-item buttons.
//
// - Calendar: memo's events are kept in a calendar called "memo" that memo creates and
//   owns. One way: memo is the source of truth, and an edit made to one of those events
//   in Calendar is overwritten on the next sync. Turning it off deletes that calendar.
// - Reminders: memo's open todos are kept in a Reminders list called "memo". Ticking one
//   off in either app ticks it in the other (syncPlan.mergeCompletion).
//
// Both are syncs, not queues: every pass compares memo, the phone and the last pass,
// and changes only the difference, so running one twice does nothing the second time.
// Every copy carries its memo item's id in its URL field, so memo recognises its own
// copies even when its record of them is lost. memo only ever touches a calendar or list
// whose id it recorded when it made it - never one of yours, whatever it's called.

export const syncPref = devicePref("memo.phoneSync", { calendar: false, reminders: false });
// The ids of the calendar and list memo made. In the keychain, like the switches, so they
// survive a reinstall and memo finds its own calendar again rather than making another.
const ownPref = devicePref<{ calendarId: string | null; listId: string | null }>("memo.phoneSync.own", {
  calendarId: null,
  listId: null,
});

const STATE = `${FileSystem.documentDirectory}phone-sync.json`;
const NAME = "memo";
const COLOR = "#E2A04A"; // memo's amber, so its events are recognisable in Calendar
const FETCH_MAX = 1000;
const DAY = 86_400_000;

// The last pass, kept in a file on the phone, never on the server.
type State = { events: Mapping; todos: Mapping };
let state: State | null = null;

async function loadState(): Promise<State> {
  if (state) return state;
  try {
    const saved = JSON.parse(await FileSystem.readAsStringAsync(STATE)) as Partial<State>;
    state = { events: saved.events ?? {}, todos: saved.todos ?? {} };
  } catch {
    state = { events: {}, todos: {} };
  }
  return state;
}

async function saveState(next: State) {
  state = next;
  await FileSystem.writeAsStringAsync(STATE, JSON.stringify(next)).catch(() => {});
}

// Everything that touches memo's copies runs one at a time, in the order asked: a pass,
// a switch flipped mid-pass, sign-out. None reads state another is halfway through.
let chain: Promise<unknown> = Promise.resolve();
function serial<T>(task: () => Promise<T>): Promise<T> {
  const run = chain.then(task, task);
  chain = run.catch(() => {});
  return run;
}

const PERMISSION_TEXT = {
  calendar: "memo can't use your calendar. You can allow it in the iPhone's Settings → memo → Calendars.",
  reminders: "memo can't use Reminders. You can allow it in the iPhone's Settings → memo → Reminders.",
};

type ReminderDetails = Parameters<Calendar.ExpoCalendarReminder["update"]>[0];

type Kind = "calendar" | "list";
const ENTITY = { calendar: Calendar.EntityTypes.EVENT, list: Calendar.EntityTypes.REMINDER };

async function ownId(kind: Kind): Promise<string | null> {
  const own = await ownPref.get();
  return kind === "calendar" ? own.calendarId : own.listId;
}

async function setOwnId(kind: Kind, id: string | null) {
  const own = await ownPref.get();
  await ownPref.set(kind === "calendar" ? { ...own, calendarId: id } : { ...own, listId: id });
}

async function findOwn(kind: Kind): Promise<Calendar.ExpoCalendar | null> {
  const id = await ownId(kind);
  if (!id) return null;
  const all = await Calendar.getCalendars(ENTITY[kind]);
  return all.find((c) => c.id === id) ?? null;
}

// Take memo's calendar or list, and everything in it, off the phone. If the list itself
// can't be deleted, memo empties it instead. Returns a sentence for the person when
// something was left behind; on a failure memo keeps its record, so it can try again.
async function removeOwn(kind: Kind): Promise<string | null> {
  const own = await findOwn(kind).catch(() => null);
  if (own) {
    try {
      await own.delete();
    } catch {
      if (kind === "calendar") return "memo couldn't remove its calendar. You can delete it in the Calendar app.";
      try {
        for (const r of await own.listReminders(null, null, null)) await r.delete();
      } catch {
        return "memo couldn't remove its todos from Reminders. You can delete the memo list in the Reminders app.";
      }
      await forget(kind);
      return "memo removed its todos. You can delete the empty memo list in the Reminders app.";
    }
  }
  await forget(kind);
  return null;
}

async function forget(kind: Kind) {
  await setOwnId(kind, null);
  const s = await loadState();
  await saveState(kind === "calendar" ? { ...s, events: {} } : { ...s, todos: {} });
}

// The Settings switches. On asks for the access it needs (full calendar access: memo
// has to find its events again to update them); off removes memo's calendar or list.
export function setCalendarSync(on: boolean): Promise<string | null> {
  return serial(async () => {
    if (on) {
      const permission = await Calendar.requestCalendarPermissions(false);
      if (!permission.granted) return PERMISSION_TEXT.calendar;
    }
    const left = on ? null : await removeOwn("calendar");
    await syncPref.set({ ...(await syncPref.get()), calendar: on });
    return left;
  });
}

export function setRemindersSync(on: boolean): Promise<string | null> {
  return serial(async () => {
    if (on) {
      const permission = await Calendar.requestRemindersPermissions();
      if (!permission.granted) return PERMISSION_TEXT.reminders;
    }
    const left = on ? null : await removeOwn("list");
    await syncPref.set({ ...(await syncPref.get()), reminders: on });
    return left;
  });
}

// Signing out, or deleting the account, takes memo's copies off the phone: its calendar,
// its list and its pending notifications belong to the account, not the phone. The
// switches stay as they are, so signing back in puts everything back.
export function clearPhone(): Promise<void> {
  if (Platform.OS !== "ios") return Promise.resolve();
  return serial(async () => {
    await cancelNotifications();
    if ((await Calendar.getCalendarPermissions(false)).granted) await removeOwn("calendar");
    if ((await Calendar.getRemindersPermissions()).granted) await removeOwn("list");
  }).catch(() => {});
}

// memo's calendar or list, made the first time and again if you deleted it. Found only
// by the id memo recorded when it made it. A new one starts with an empty mapping: the
// tags on what's in it rebuild the rest.
async function ensureOwn(kind: Kind): Promise<{ container: Calendar.ExpoCalendar; fresh: boolean }> {
  const found = await findOwn(kind);
  if (found) return { container: found, fresh: false };
  const container = await Calendar.createCalendar({ title: NAME, color: COLOR, entityType: ENTITY[kind] });
  await setOwnId(kind, container.id);
  return { container, fresh: true };
}

function oldestOf(items: Derivation[]): string | null {
  return items.length ? items[items.length - 1].created_at : null;
}

// A copy recognised by its tag has no record of when its item was made; take it from
// the item, so the page-boundary rule (syncPlan.isGone) works for it too.
function fillCreated(mapping: Mapping, items: Derivation[]) {
  for (const item of items) {
    const m = mapping[item.id];
    if (m && !m.created) mapping[item.id] = { ...m, created: item.created_at };
  }
}

// Progress is written down as it's made, so a pass killed halfway loses little - and the
// tags mean the next pass recognises anything added after the last save.
function every(n: number, write: () => Promise<void>) {
  let done = 0;
  return async () => {
    done += 1;
    if (done % n === 0) await write();
  };
}

async function syncCalendar(s: State, items: Derivation[], truncated: boolean): Promise<State> {
  const { container, fresh } = await ensureOwn("calendar");
  const mapping: Mapping = fresh ? {} : { ...s.events };
  try {
    await applyEvents(container, mapping, items, truncated, every(10, () => saveState({ ...s, events: mapping })));
  } catch {
    // the next pass carries on from here
  }
  return { ...s, events: mapping };
}

async function applyEvents(
  container: Calendar.ExpoCalendar,
  mapping: Mapping,
  items: Derivation[],
  truncated: boolean,
  progress: () => Promise<void>,
) {
  const now = Date.now();
  const window = { from: new Date(now - 30 * DAY), to: new Date(now + 365 * DAY) };
  const onPhone = await container.listEvents(window.from, window.to);
  const byId = new Map(onPhone.map((e) => [e.id, e]));
  const phone = new Map(onPhone.map((e) => [e.id, { id: e.id, itemId: itemIdFromTag(e.url) }]));

  const healed = heal(mapping, phone);
  Object.assign(mapping, healed.mapping);
  for (const id of healed.duplicates) {
    await byId.get(id)?.delete();
    byId.delete(id);
    phone.delete(id);
  }
  fillCreated(mapping, items);
  for (const [itemId, m] of Object.entries(mapping)) {
    const e = byId.get(m.id);
    if (m.start == null && e) mapping[itemId] = { ...m, start: new Date(e.startDate).getTime() };
  }

  const plan = planEvents(items, mapping, phone, window, { oldest: oldestOf(items), truncated });
  for (const c of plan.create) {
    const created = await container.createEvent({
      title: c.fields.title,
      startDate: c.fields.startDate,
      endDate: c.fields.endDate,
      url: tagFor(c.itemId),
      ...(c.fields.location ? { location: c.fields.location } : {}),
    });
    mapping[c.itemId] = { id: created.id, hash: c.hash, created: c.created, start: c.fields.startDate.getTime() };
    await progress();
  }
  for (const u of plan.update) {
    await byId.get(u.phoneId)?.update({
      title: u.fields.title,
      startDate: u.fields.startDate,
      endDate: u.fields.endDate,
      url: tagFor(u.itemId),
      // null clears it: a location removed in memo is removed from Calendar too.
      location: u.fields.location,
    });
    mapping[u.itemId] = { ...mapping[u.itemId], hash: u.hash, start: u.fields.startDate.getTime() };
    await progress();
  }
  // Only the window was listed; a copy to remove from outside it is looked for on the day
  // it was put (one day at a time: EventKit won't search a range longer than four years).
  for (const r of plan.remove) {
    if (!r.phoneId || r.listed || r.start == null || byId.has(r.phoneId)) continue;
    for (const e of await container.listEvents(new Date(r.start - DAY), new Date(r.start + DAY))) byId.set(e.id, e);
  }
  for (const r of plan.remove) {
    if (r.phoneId) await byId.get(r.phoneId)?.delete();
    delete mapping[r.itemId];
  }
  for (const id of plan.detach) mapping[id] = { ...mapping[id], detached: true };
}

// Returns whether memo's own data changed (a todo ticked in Reminders), so the app
// can refresh what it shows.
async function syncReminders(api: Api, s: State, items: Derivation[], truncated: boolean): Promise<[State, boolean]> {
  const { container, fresh } = await ensureOwn("list");
  const mapping: Mapping = fresh ? {} : { ...s.todos };
  let changed = false;
  try {
    const progress = every(10, () => saveState({ ...s, todos: mapping }));
    changed = await applyReminders(api, container, mapping, items, truncated, progress);
  } catch {
    // as with events: progress so far is kept
  }
  return [{ ...s, todos: mapping }, changed];
}

async function applyReminders(
  api: Api,
  container: Calendar.ExpoCalendar,
  mapping: Mapping,
  items: Derivation[],
  truncated: boolean,
  progress: () => Promise<void>,
): Promise<boolean> {
  const onPhone = await container.listReminders(null, null, null);
  const byId = new Map(onPhone.flatMap((r) => (r.id ? [[r.id, r] as const] : [])));
  const phone = new Map(
    [...byId].map(([id, r]) => [id, { id, completed: !!r.completed, itemId: itemIdFromTag(r.url) }]),
  );

  const healed = heal(mapping, phone);
  Object.assign(mapping, healed.mapping);
  for (const id of healed.duplicates) {
    await byId.get(id)?.delete();
    byId.delete(id);
    phone.delete(id);
  }
  fillCreated(mapping, items);

  const plan = planReminders(items, mapping, phone, { oldest: oldestOf(items), truncated });
  for (const c of plan.create) {
    const created = await container.createReminder({
      title: c.fields.title,
      url: tagFor(c.itemId),
      ...(c.fields.notes ? { notes: c.fields.notes } : {}),
      ...(c.fields.dueDate ? { dueDate: c.fields.dueDate } : {}),
    });
    if (created.id) mapping[c.itemId] = { id: created.id, hash: c.hash, created: c.created, completed: false };
    await progress();
  }
  for (const u of plan.update) {
    const f = u.fields;
    await byId.get(u.phoneId)?.update({
      // null clears a field: notes or a due date removed in memo go from Reminders too.
      // expo-calendar supports that (it sends null fields as nullableFields), but its
      // reminder types don't admit null, hence the cast.
      ...(f ? ({ title: f.title, url: tagFor(u.itemId), notes: f.notes, dueDate: f.dueDate } as ReminderDetails) : {}),
      ...(u.completed !== undefined ? { completed: u.completed, ...(u.completed ? { completionDate: new Date() } : {}) } : {}),
    });
    mapping[u.itemId] = { ...mapping[u.itemId], hash: u.hash };
    await progress();
  }
  let changed = false;
  for (const p of plan.pull) {
    try {
      await api.reviewDerivation(p.itemId, { completed: p.completed });
      changed = true;
    } catch {
      // Left as it was; the base below stays, so the next pass tries again.
      delete plan.settled[p.itemId];
    }
  }
  for (const [itemId, completed] of Object.entries(plan.settled)) {
    if (mapping[itemId]) mapping[itemId] = { ...mapping[itemId], completed };
  }
  for (const r of plan.remove) {
    if (r.phoneId) await byId.get(r.phoneId)?.delete();
    delete mapping[r.itemId];
  }
  for (const id of plan.detach) mapping[id] = { ...mapping[id], detached: true };
  return changed;
}

let waiting: Promise<boolean> | null = null;
let newest: Api | null = null;

// One pass over everything that puts memo on the phone: event notifications, the
// calendar and Reminders. Items are fetched once and shared. Passes queue behind each
// other and behind the switches; any number asked for while one waits are that one pass,
// run with the newest Api it was given.
export function syncPhone(api: Api): Promise<boolean> {
  if (Platform.OS !== "ios") return Promise.resolve(false);
  newest = api;
  if (waiting) return waiting;
  const pass = serial(async () => {
    waiting = null;
    return newest ? runPass(newest) : false;
  });
  waiting = pass;
  return pass;
}

async function runPass(api: Api): Promise<boolean> {
  try {
    const { items, truncated } = await api.derivationPages(FETCH_MAX);
    await syncNotifications(items);
    const prefs = await syncPref.get();
    let s = await loadState();
    let changed = false;
    if (prefs.calendar && (await Calendar.getCalendarPermissions(false)).granted) {
      s = await syncCalendar(s, items, truncated).catch(() => s);
    }
    if (prefs.reminders && (await Calendar.getRemindersPermissions()).granted) {
      const [next, pulled] = await syncReminders(api, s, items, truncated).catch((): [State, boolean] => [s, false]);
      s = next;
      changed = pulled;
    }
    await saveState(s);
    return changed;
  } catch {
    // Offline or signed out mid-pass: what's on the phone stays; the next pass fixes it.
    return false;
  }
}

// Runs a pass on sign-in, after anything memo changes (refreshKey), when a switch in
// Settings flips, and when the app comes back to the foreground - that last one is how
// a todo ticked in Reminders reaches memo. Debounced: a burst of changes is one pass.
export function usePhoneSync(api: Api | null, refreshKey: number, onChanged: () => void) {
  const sync = syncPref.use();
  const notify = remindersPref.use();
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const changed = useRef(onChanged);
  useEffect(() => {
    changed.current = onChanged;
  }, [onChanged]);
  useEffect(() => {
    if (!api) return;
    const run = () => {
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => {
        void syncPhone(api).then((pulled) => pulled && changed.current());
      }, 1500);
    };
    run();
    const sub = AppState.addEventListener("change", (s) => s === "active" && run());
    return () => {
      sub.remove();
      if (timer.current) clearTimeout(timer.current);
    };
  }, [api, refreshKey, sync.calendar, sync.reminders, notify.on, notify.minutes]);
}
