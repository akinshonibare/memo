# 0024 — A photo becomes text, then flows through the pipeline like a voice note

**Status:** active
**Date:** 2026-09-28

## Problem

People capture things by photographing them: a flyer, a whiteboard, a receipt, a page.
memo only took text and voice. The brief puts images (with vision extraction) in V2.
What a photo capture needs to do is the same as any other: be searchable in Ask, and
produce todos, events and ideas that land in the Planner.

## Options

| Option | What it buys | What it costs |
| --- | --- | --- |
| **Describe to text, then the existing pipeline**: a `describe` stage has Claude vision write a description plus every legible line of text into the capture's `text`, exactly where a transcript goes; extract and embed run unchanged | One new stage, and nothing downstream changes: extraction, chunking, retrieval, citations, Ask and the evals all work on text they already understand. Each photo's text is stamped with a describer version, like the extractor | What the photo *looks like* is only searchable through the words the model chose. A detail it left out can't be found. One vision call per photo (~$0.013) |
| **Multimodal embeddings** (for example Voyage's multimodal model, image and text in one vector space) | Search can match visual similarity the description missed | A second embedding space with its own model version and re-embed path. Ask's answer still needs *text* for Claude to read, so we'd describe the photo anyway. Extraction needs text too |
| **Send the image to every consumer**: extraction and Ask get the image itself | Nothing is lost in translation | Every Ask question that retrieves a photo pays vision tokens again, extraction gets two modes, the evals would need image fixtures, and retrieval still needs some text to find the photo in the first place |
| **Photos as attachments to text notes** | No new capture kind | Doesn't fit "snap the flyer" (there is no note to attach to), and the photo still needs describing to be searchable |

## Decision

**A photo is a capture of kind `image`. A `describe` stage (Claude vision, prompt
`describe-v1`) turns it into text, stored as the capture's text, and the rest of the
pipeline runs unchanged.** The bytes live in the bucket beside recordings, and the app
shows the original photo on the note.

Mechanics:

- The app resizes to 1568px on the long edge and re-encodes as JPEG before upload. That
  is Claude's effective resolution, and iPhone photos default to HEIC, which vision
  doesn't read. The server accepts only JPEG, PNG and WebP.
- Uploads reuse the presigned `recordings` flow. Capture `kind` comes from the uploaded
  mime type. Migration 0010 adds `image` and a `describing` status.
- The vision call is priced and logged like every other LLM call (`stage=describe`), and
  it goes through the same retry and dead-letter policy. A refusal or an unreadable image
  is a visible failed capture, not a silent empty note.

## What we're accepting

- **The description is lossy.** Search only sees what the model wrote down. That is the
  same trade we already make with transcripts, and the original is always one tap away.
- **The table is called `recordings` and now holds photos.** Renaming it would change the
  `recording_id` field the installed app builds send. The model's docstring says what it
  is. Rename it at the next breaking API version.
- **Descriptions aren't evaluated yet.** There's no golden set for photos. A bad
  description shows up as bad extraction, which the extraction eval only catches for
  text inputs.

## Revisit when

- "I know I took a photo of it" searches fail on things the description left out. That
  is the trigger for multimodal embeddings as a second retrieval branch, fused with RRF
  like the others.
- Photos are a meaningful share of captures: add describe cases to the evals (photo in,
  expected key facts out).
- The API gets a v2: rename `recordings` → `uploads` with it.

## Interview angle

> **"Add image support to a RAG system. How?"**

Decide where the modality boundary goes. I convert at the edge: the image becomes text
once, at ingest, with a versioned vision prompt, and everything downstream (chunking,
hybrid retrieval, citations, extraction, evals) stays single-modality. That's one new
stage instead of a second embedding space and a second path through every consumer, and
it's the same trade I already made for audio with transcription. The cost is that
retrieval can only find what the description mentions, so the original stays stored and
shown. If visual-similarity search ever matters, I'd add image embeddings as another
retrieval branch fused with RRF, not replace the text path.
