# 0009 — uv, a `src/` layout, and a pinned Python 3.12

**Status:** active
**Date:** 2026-08-18

## Problem

Three machines have to agree on what "the app" is: a laptop, CI ([#40](https://github.com/akinshonibare/memo/issues/40)),
and Railway ([#42](https://github.com/akinshonibare/memo/issues/42)). Disagreement between
them is the single most common source of "works on my machine", and in this project it
would be a pure waste of the learning budget — time spent on environment archaeology is
time not spent on extraction, retrieval, or streaming.

Two separate questions, decided together because they land in the same file:

1. What resolves, locks, and installs dependencies — *and the interpreter*?
2. Where does the package live, such that tests exercise the thing that ships?

## Options

### Dependency and interpreter management

| Option | What it buys | What it costs |
| --- | --- | --- |
| **pip + `requirements.txt`** | Nothing to learn. Every host on earth understands it. | No lockfile without bolting on `pip-tools`. Dev/prod split is manual convention. The interpreter is whatever happened to be on `PATH`, which is exactly the variable we're trying to remove. |
| **Poetry** | The incumbent — most existing Python codebases you'd join use it. Lockfile, dependency groups, `pyproject.toml`. | Slow resolver. A history of fighting packaging PEPs rather than implementing them. Manages its own virtualenvs, which is a second mental model on top of the one you already have. Still doesn't manage the interpreter. |
| **uv** | Resolves fast enough to stop being a thing you think about. `uv.lock` is cross-platform and hash-pinned. **Manages the interpreter too**, so "which Python" stops being a category of bug rather than a bug you fix repeatedly. Standards-native `pyproject.toml`, no bespoke sections. | Young, and single-vendor (Astral). Some hosts' buildpacks still sniff for `requirements.txt` or `poetry.lock` and won't autodetect a uv project — which can mean hand-writing a Dockerfile for deploy. |

### Package layout

| Option | What it buys | What it costs |
| --- | --- | --- |
| **Flat (`memo/` at the repo root)** | One less directory. Marginally shorter paths. | Running pytest from the root puts the root on `sys.path`, so tests import the *source tree*, not the installed package. Any packaging defect — a module missing from the wheel, a bad `__init__`, a data file that never got included — is invisible locally and surfaces at deploy. |
| **`src/` (`src/memo/`)** | The only way to import `memo` is to have installed it. Tests therefore exercise the same artifact production runs, and a broken build fails in CI instead of on Railway. | One more directory level. Requires the project to actually be installable, which is a constraint — but a constraint we want. |

## Decision

uv, with a `src/` layout and Python pinned to 3.12 via `.python-version` and a
`requires-python = ">=3.12,<3.13"` bound.

3.12 rather than something newer because Phase 2 brings `faster-whisper`/`ctranslate2`-shaped
dependencies, and native-extension wheels lag new interpreter releases by months. Nothing we
need lives above 3.12.

## What we're accepting

**A possible hand-written Dockerfile.** If Railway's buildpack doesn't recognise a uv
project, we own the build definition rather than getting one for free. That's a real cost
paid at [#42](https://github.com/akinshonibare/memo/issues/42), and it's acceptable because
an explicit Dockerfile is a better answer at the quality bar this repo holds anyway.

**A bet on a young tool.** If uv were abandoned tomorrow, the exit is cheap — `pyproject.toml`
is standard and `uv export` produces a `requirements.txt` — but `uv.lock` itself is not a
standard format and would not survive the move intact.

**An upper bound on `requires-python`,** which is bad practice for a *library* because it
poisons resolution for anything depending on you. memo is an application; nothing depends on
it, so the cap is documentation plus a guardrail. If this repo ever publishes a library, that
package does not inherit this rule.

## Revisit when

- A deploy target cannot build a uv project *and* a Dockerfile is not an acceptable answer.
- A dependency we actually need requires an interpreter above 3.12 — at which point bump the
  pin, don't remove it.

## Interview angle

> **"What does a lockfile guarantee that a `requirements.txt` doesn't?"**

A requirements file is a list of *constraints* — it says `fastapi>=0.141`, and two installs a
week apart resolve to different trees. A lockfile is a list of *resolutions*: every package
in the transitive closure, at one exact version, with a hash. That buys three distinct
things people tend to conflate. **Reproducibility** — the same input produces the same tree
on your laptop, in CI, and in prod, so a green CI run is evidence about the artifact that
ships. **Integrity** — the hashes mean a compromised or re-uploaded package fails the install
instead of silently substituting itself. **Auditability** — a diff on `uv.lock` shows exactly
which transitive dependency changed and when, which is how you answer "did anything move
between the build that worked and the one that didn't". The trap to name: a lockfile pins
*Python packages*, not the interpreter, the C libraries they link against, or the OS. That's
why the interpreter is pinned separately, and why the container image tag matters too — a
lockfile alone is necessary and not sufficient.

> **"Why a `src/` layout?"**

Because without it your tests import the source directory instead of the installed package,
so packaging bugs are invisible until deploy. It's a cheap way to make the local test run and
the production artifact the same object.
