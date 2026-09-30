# 0056 — A photo with your words: your words first, the description after

**Status:** active
**Date:** 2026-09-30
**Amends:** [0024](0024-photos-become-text-then-flow-through.md)

## Problem

A photo became a note on its own: Claude described it, and that description was all
memo knew. Akin: *I don't want to just upload images, I want to upload images usually
with text, for added context.* The photo of a waterfall means nothing until you say
"Kemi says we should go in June".

## Options

| Option | Buys | Costs |
| --- | --- | --- |
| Two notes, a text note and a photo note, linked | No schema change | Two rows for one thought; extraction reads them apart, so "book flights by Friday" never meets the waterfall |
| Put your words straight into `text` | Least code | The describe stage writes `text`; a re-run would lose or double the words |
| **A `caption` column, and `text` rebuilt from it: your words, then the description** | One note; every stage reads both; reprocessing is idempotent | A column (migration 0024), and the describer prompt bumps (`describe-v1.1`) |

## Decision

**A photo may carry a `caption` (migration 0024; only on photos, by a CHECK). The
describe stage builds `text` as your words, a blank line, then the description; the
describer also sees your words as context, so it says what matters about the photo
without repeating them.** Until the photo is described, the note reads as your words.

In the app, the note sheet takes a photo (camera or library) beside the text; + → Take
or Choose a photo opens that sheet with the photo attached, and so does a photo shared
from another app, with any shared text prefilled. A photo alone still saves.

## What we're accepting

- **One photo per note.** Several would need a photos table; not asked for.
- **The describer's prompt changed without an eval** (there is no describe eval): the
  change adds context, and the note keeps your words verbatim whatever the model says.
- **Voice notes don't take a caption.** A voice note is already your words.

## Revisit when

- Several photos per note are wanted: a `capture_media` table.
- Descriptions start echoing the caption: tighten the prompt, and add a describe eval.

## Interview angle

> **"A derived field depends on user input and on a model's output. How do you store it?"**

Store the user's input as its own column and derive the combined field from it on every
run, so a retry or a reprocess rebuilds the same value instead of appending to it. Keep
what the user wrote verbatim and first; the model's text is secondary and replaceable.
