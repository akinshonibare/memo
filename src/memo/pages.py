"""Public pages: the privacy policy TestFlight and the App Store link to.

Served by the API itself so the policy lives next to the code it describes: when a
processor or a retention rule changes, the same pull request changes this page.
"""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from memo.config import get_settings

router = APIRouter(include_in_schema=False)

UPDATED = "28 September 2026"

_PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>memo · Privacy</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font: 16px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         max-width: 680px; margin: 0 auto; padding: 32px 20px 64px; }}
  h1 {{ font-size: 28px; margin-bottom: 4px; }}
  h2 {{ font-size: 18px; margin-top: 32px; }}
  .muted {{ opacity: .6; font-size: 14px; }}
  li {{ margin: 4px 0; }}
</style></head><body>
<h1>memo privacy policy</h1>
<p class="muted">Last updated {updated}</p>

<p>memo is a personal notes app: you write, record or photograph things, and it pulls
out todos, events and ideas and answers questions about your notes. This page says what
it stores, who processes it, and how to get it back or delete it.</p>

<h2>What memo stores</h2>
<ul>
  <li><b>Your Apple sign-in</b>: Apple's anonymous identifier for you, and your name and
      email if you chose to share them.</li>
  <li><b>What you capture</b>: typed notes, voice recordings and their transcripts,
      photos and their descriptions.</li>
  <li><b>What memo makes from it</b>: todos, events and ideas; search indexes of your
      notes; your questions and memo's answers.</li>
  <li><b>Settings</b>: the name memo calls you, your voice choice, your timezone.</li>
  <li><b>Usage records</b>: which AI model handled each request, how many tokens, and
      what it cost. Not the content itself.</li>
</ul>
<p>Spoken questions to Ask are transcribed and not stored as recordings.</p>

<h2>Who processes it</h2>
<p>Your content is sent to these services only to do the job listed.</p>
<ul>
  <li><b>Anthropic</b> (Claude): pulling items out of notes, describing photos,
      answering your questions.</li>
  <li><b>Deepgram</b>: turning voice into text and reading answers aloud.</li>
  <li><b>Voyage AI</b>, via MongoDB Atlas: search indexes of your notes.</li>
  <li><b>Railway</b>: hosting, the database and file storage.</li>
  <li><b>Apple</b>: signing you in.</li>
</ul>
<p><b>Model training.</b> Anthropic does not train on content sent through its API.
Deepgram does by default; memo opts every request out of Deepgram's model-improvement
program. {voyage}</p>

<h2>What memo does not do</h2>
<ul>
  <li>No advertising, no selling or sharing data for marketing, no tracking across
      apps or websites, no analytics SDKs.</li>
</ul>

<h2>Who can see it</h2>
<p>Your notes are separated from every other account's at the database level. memo is
run by one developer, who operates the database and so could technically access it;
it is not looked at except to fix a problem you report.</p>

<h2>Keeping and deleting</h2>
<ul>
  <li>Data is kept until you delete it.</li>
  <li>In the app: delete a single note, <b>Profile → Delete all data</b>, or
      <b>Profile → Delete account</b>, which removes everything including the account.
      </li>
  <li>Deleted data can remain in encrypted database backups for up to about four weeks,
      after which the backups expire.</li>
  <li><b>Profile → Export everything</b> gives you all of it as a file, any time.</li>
</ul>

<h2>Children</h2>
<p>memo is not intended for children under 13.</p>

<h2>Contact</h2>
<p>{contact}</p>
</body></html>
"""


@router.get("/privacy", response_class=HTMLResponse)
async def privacy() -> str:
	email = get_settings().support_email
	contact = (
		f'Questions or requests: <a href="mailto:{email}">{email}</a>'
		if email
		else "Questions or requests: use the feedback option in TestFlight."
	)
	voyage = (
		"memo's Voyage AI account is opted out of training."
		if get_settings().voyage_training_opted_out
		else "Voyage AI may use indexed text to improve its models under its own terms."
	)
	return _PAGE.format(updated=UPDATED, contact=contact, voyage=voyage)
