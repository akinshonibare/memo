// The decisions behind putting memo's events and todos on the phone (ADR 0034), kept
// apart from EventKit so they can be checked on their own. Every function here is pure:
// given what memo has, what the phone has, and what was true at the last sync, it says
// what to change. Running a plan and then planning again yields nothing to do.

export type Item = {
  id: string;
  kind: "todo" | "event" | "idea";
  status: string;
  created_at: string;
  completed_at: string | null;
  payload: Record<string, unknown>;
};

// What the last sync left on the phone for one memo item. `detached`: you deleted the
// phone's copy yourself, so memo stops putting it back. `start` (events): where to look
// for it when it has to be removed from outside the window memo lists.
export type Mapped = {
  id: string;
  hash: string;
  created: string;
  completed?: boolean;
  detached?: boolean;
  start?: number;
};
export type Mapping = Record<string, Mapped>;

// What the phone has now, by its own id. `itemId` is the memo item it was made from,
// read back from the tag memo writes into its URL field.
export type PhoneEvent = { id: string; itemId?: string | null };
export type PhoneReminder = { id: string; completed: boolean; itemId?: string | null };

// Every copy memo puts on the phone carries its memo item's id, so memo can recognise
// its own copies again even when its record of them is gone (a reinstall, a pass killed
// halfway) instead of adding them a second time.
const TAG = "memo://item/";
export function tagFor(itemId: string): string {
  return `${TAG}${itemId}`;
}
export function itemIdFromTag(url: string | null | undefined): string | null {
  return url && url.startsWith(TAG) ? url.slice(TAG.length) || null : null;
}

// Rebuild the mapping from the tags on the phone: an item memo has no record of, but
// whose tagged copy is there, is mapped to that copy (its fingerprint left blank, so the
// next plan refreshes it); a second copy of the same item is a duplicate to remove.
export function heal<T extends { id: string; itemId?: string | null; completed?: boolean }>(
  mapping: Mapping,
  phone: Map<string, T>,
): { mapping: Mapping; duplicates: string[] } {
  const next: Mapping = { ...mapping };
  const duplicates: string[] = [];
  const claimed = new Set(Object.values(next).map((m) => m.id));
  for (const item of phone.values()) {
    const itemId = item.itemId;
    if (!itemId) continue;
    const mapped = next[itemId];
    if (mapped && mapped.id === item.id) continue;
    if (mapped && !mapped.detached && phone.has(mapped.id)) {
      // Already mapped to another copy that's still there: this one is extra.
      if (!claimed.has(item.id)) duplicates.push(item.id);
      continue;
    }
    next[itemId] = { id: item.id, hash: "", created: mapped?.created ?? "", completed: item.completed ?? false };
    claimed.add(item.id);
  }
  return { mapping: next, duplicates };
}

export type EventFields = { title: string; startDate: Date; endDate: Date; location: string | null };
export type ReminderFields = { title: string; notes: string | null; dueDate: Date | null };

const HOUR = 3_600_000;

export function eventFields(item: Item): EventFields | null {
  const p = item.payload;
  if (typeof p.starts_at !== "string") return null;
  const start = new Date(p.starts_at);
  if (Number.isNaN(start.getTime())) return null;
  const endRaw = typeof p.ends_at === "string" ? new Date(p.ends_at) : null;
  const end = endRaw && !Number.isNaN(endRaw.getTime()) && endRaw > start ? endRaw : new Date(start.getTime() + HOUR);
  return {
    title: String(p.title ?? "Event"),
    startDate: start,
    endDate: end,
    location: typeof p.location === "string" && p.location ? p.location : null,
  };
}

export function reminderFields(item: Item): ReminderFields {
  const p = item.payload;
  return {
    title: String(p.title ?? "Todo"),
    notes: typeof p.notes === "string" && p.notes ? p.notes : null,
    // A todo has a day, not a time; 09:00 that day is when Reminders shows it due.
    dueDate: typeof p.due === "string" ? new Date(`${p.due}T09:00:00`) : null,
  };
}

// A fingerprint of the fields memo owns: a change here is pushed to the phone.
export function hashOf(fields: EventFields | ReminderFields): string {
  return JSON.stringify(fields, (_k, v) => (v instanceof Date ? v.getTime() : v));
}

// Whether a mapped item memo no longer lists is really gone (deleted with all your
// data) or just older than the page of items that was fetched. Only the first is
// removed from the phone; the second is left alone.
// Strictly newer: items created in one transaction share a timestamp, so one exactly at
// the page boundary may be the unread half of its group.
export function isGone(mapped: Mapped, oldestFetched: string | null, truncated: boolean): boolean {
  if (!truncated) return true;
  return oldestFetched !== null && Date.parse(mapped.created) > Date.parse(oldestFetched);
}

export type EventPlan = {
  create: { itemId: string; fields: EventFields; hash: string; created: string }[];
  update: { itemId: string; phoneId: string; fields: EventFields; hash: string }[];
  // `listed`: the copy was in the window memo read; otherwise it's looked up by `start`.
  remove: { itemId: string; phoneId: string | null; listed: boolean; start?: number }[];
  detach: string[];
};

// Events: memo is the source of truth, one way. The phone's copies live in a calendar
// memo owns, so nothing here can touch an event you made yourself. Within the window:
// new events are added, changed ones updated, rejected or deleted ones removed, and one
// you deleted from Calendar is not put back.
export function planEvents(
  items: Item[],
  mapping: Mapping,
  phone: Map<string, PhoneEvent>,
  window: { from: Date; to: Date },
  fetched: { oldest: string | null; truncated: boolean },
): EventPlan {
  const plan: EventPlan = { create: [], update: [], remove: [], detach: [] };
  const byId = new Map(items.map((i) => [i.id, i]));
  for (const item of items) {
    if (item.kind !== "event" || item.status === "rejected") continue;
    const fields = eventFields(item);
    if (!fields) continue;
    const inWindow = fields.startDate >= window.from && fields.startDate <= window.to;
    const hash = hashOf(fields);
    const mapped = mapping[item.id];
    if (!mapped) {
      if (inWindow) plan.create.push({ itemId: item.id, fields, hash, created: item.created_at });
    } else if (mapped.detached) {
      continue;
    } else if (phone.has(mapped.id)) {
      // Updated wherever it now falls, so an event moved out of the window still moves.
      if (mapped.hash !== hash) plan.update.push({ itemId: item.id, phoneId: mapped.id, fields, hash });
    } else if (inWindow) {
      // Only the window is listed from the phone: missing inside it means you deleted it.
      plan.detach.push(item.id);
    }
  }
  for (const [itemId, mapped] of Object.entries(mapping)) {
    const item = byId.get(itemId);
    const rejected = item?.status === "rejected";
    if (!rejected && (item || !isGone(mapped, fetched.oldest, fetched.truncated))) continue;
    plan.remove.push({
      itemId,
      phoneId: mapped.detached ? null : mapped.id,
      listed: phone.has(mapped.id),
      start: mapped.start,
    });
  }
  return plan;
}

// Completion is the one thing that flows both ways: tick a todo in memo or in
// Reminders, and the other follows. A three-way merge against the state at the last
// sync says which side changed; if both did, and disagree, done wins - undoing a tick
// is rarer than making one, and a wrongly-open todo is easy to see.
export function mergeCompletion(
  memoDone: boolean,
  phoneDone: boolean,
  base: boolean,
): { result: boolean; pushToPhone: boolean; pullToMemo: boolean } {
  if (memoDone === phoneDone) return { result: memoDone, pushToPhone: false, pullToMemo: false };
  const memoMoved = memoDone !== base;
  const phoneMoved = phoneDone !== base;
  if (memoMoved && !phoneMoved) return { result: memoDone, pushToPhone: true, pullToMemo: false };
  if (phoneMoved && !memoMoved) return { result: phoneDone, pushToPhone: false, pullToMemo: true };
  return { result: true, pushToPhone: !phoneDone, pullToMemo: !memoDone };
}

export type ReminderPlan = {
  create: { itemId: string; fields: ReminderFields; hash: string; created: string }[];
  update: { itemId: string; phoneId: string; fields?: ReminderFields; hash: string; completed?: boolean }[];
  pull: { itemId: string; completed: boolean }[];
  remove: { itemId: string; phoneId: string | null }[];
  detach: string[];
  // The completion both sides now agree on, for the next merge's base.
  settled: Record<string, boolean>;
};

// Todos: open todos are added to memo's own Reminders list; content flows memo → phone;
// completion flows both ways (mergeCompletion). A done todo that was never on the phone
// isn't added just to be ticked.
export function planReminders(
  items: Item[],
  mapping: Mapping,
  phone: Map<string, PhoneReminder>,
  fetched: { oldest: string | null; truncated: boolean },
): ReminderPlan {
  const plan: ReminderPlan = { create: [], update: [], pull: [], remove: [], detach: [], settled: {} };
  const byId = new Map(items.map((i) => [i.id, i]));
  for (const item of items) {
    if (item.kind !== "todo" || item.status === "rejected") continue;
    const memoDone = !!item.completed_at;
    const fields = reminderFields(item);
    const hash = hashOf(fields);
    const mapped = mapping[item.id];
    if (!mapped) {
      if (!memoDone) plan.create.push({ itemId: item.id, fields, hash, created: item.created_at });
      continue;
    }
    if (mapped.detached) continue;
    const rem = phone.get(mapped.id);
    if (!rem) {
      plan.detach.push(item.id);
      continue;
    }
    const merge = mergeCompletion(memoDone, rem.completed, mapped.completed ?? false);
    plan.settled[item.id] = merge.result;
    if (merge.pullToMemo) plan.pull.push({ itemId: item.id, completed: merge.result });
    const contentChanged = mapped.hash !== hash;
    if (contentChanged || merge.pushToPhone) {
      plan.update.push({
        itemId: item.id,
        phoneId: mapped.id,
        hash,
        ...(contentChanged ? { fields } : {}),
        ...(merge.pushToPhone ? { completed: merge.result } : {}),
      });
    }
  }
  for (const [itemId, mapped] of Object.entries(mapping)) {
    const item = byId.get(itemId);
    const rejected = item?.status === "rejected";
    if (!rejected && (item || !isGone(mapped, fetched.oldest, fetched.truncated))) continue;
    plan.remove.push({ itemId, phoneId: mapped.detached || !phone.has(mapped.id) ? null : mapped.id });
  }
  return plan;
}
