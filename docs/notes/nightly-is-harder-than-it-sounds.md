# "Nightly" is harder than it sounds, and resurfacing is a ranking problem

> **DRAFT written by Claude for issues #38/#39.** Rewrite in your own words on the
> learning pass.

## In one sentence

A job that runs "at 4am" runs at 4am *somewhere*; memo runs an hourly job that asks
each user "is it 4am on your clock right now?", which makes timezones and daylight
saving a non-event — and the one idea it resurfaces is chosen by a score, not a dice
roll.

## What problem does it solve / what did people do before it

Cron speaks UTC. A single `0 4 * * *` fires at 04:00 UTC, which is 05:00 in London in
winter and 06:00 in summer, and the wrong hour for anyone elsewhere. The naive fixes —
one cron per timezone, or storing a UTC hour per user and recomputing it twice a year —
break the first time the clocks change, silently, at 4am, when nobody is watching.

The trick is to stop trying to compute *when* to run and instead run often and ask
*whether*. The worker runs every hour and, per user, converts "now" into their zone
with `zoneinfo` and checks the hour. When the clocks go back the hour that is 04:00
locally moves from 03:00 UTC to 04:00 UTC and the check just answers yes an hour later.
The test walks 24 hours across both DST boundaries and asserts exactly one hit per day.
(The one hour that genuinely repeats on the fall-back night is 01:00–02:00; picking
04:00 sidesteps it.)

Resurfacing is the other half. "Show one old idea each morning" is a ranking problem
because *which* one is the whole product: too recent and it isn't a resurfacing, shown
last week and it's nagging, unrelated to anything current and it's noise. memo scores
each candidate idea as `ln(1 + age_days)` plus twice its cosine similarity to the
centroid of this week's captures (when embeddings exist), excludes anything shown in
the last 60 days and anything under three days old, and takes exactly one. The row in
`resurfacings` is what makes "not again for a while" enforceable. Rebuilding the same
day keeps the idea already shown — the view is stable for the day.

No guilt mechanics (brief): a todo whose day has passed is still just on the list;
nothing is counted, labelled overdue, or streaked.

## The thing I got wrong first

Thinking of the schedule as the hard part. The hard part is that "morning" belongs to
the user, not the server, and every design that puts the timezone arithmetic in the
schedule rather than in the check eventually runs at the wrong hour for someone.

## How I'd explain it to someone in an interview (30 seconds, out loud)

Never schedule "nightly" in UTC for users who aren't in UTC. Run hourly and ask, per
user, whether it's the build hour on their own clock via a proper timezone library —
daylight saving then needs no code, because the conversion changes and the question
doesn't. Pick a build hour that doesn't repeat on the fall-back night. For resurfacing,
treat it as ranking: score by age and by similarity to what the user is currently
writing about, enforce a cooldown with a table of what was shown when, and return one
item — rationing is the feature.
