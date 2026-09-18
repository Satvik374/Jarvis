"""The skills Jarvis ships with.

These are seeded into the library on first use and never overwrite a saved
version, so they can be edited like any other skill. Each one encodes a
procedure that is easy to get wrong from first principles and cheap to follow
once written down - which is exactly the case for a skill.
"""

RESEARCH_BRIEF = """---
name: research-brief
description: Research a question across the web and produce a short brief with real sources.
when_to_use: research, look up, find out, compare, investigate, sources, cite, what is the latest
tools: web_search, read_url, extract_web_data, write_file
---

# Research brief

## Steps
1. Restate the question as one sentence, and note the decision it feeds. A brief
   that does not change a decision is trivia.
2. `web_search` with the *specific* version of the query, not the vague one.
   Search for the noun the answer lives in ("X pricing 2026"), not the question
   as asked ("how much does X cost").
3. For anything that matters, open the page with `read_url` and read it. A search
   snippet is a lead, never a source.
4. `extract_web_data` when the page is a table or list you need to compare.
5. Write the brief with `write_file` to a named path the user can find again.

## Shape of the brief
- **Answer first**, in two sentences, before any detail.
- Then 3-6 bullets of evidence, each with the URL it came from.
- Then the caveats: what you could not confirm, and what would change the answer.
- Close with the source list.

## Rules
- Never state a fact you did not read. "I could not confirm X" is a real answer
  and far more useful than a confident guess.
- Prefer primary sources (vendor docs, filings, official posts) over summaries
  of them. When two sources disagree, say so and cite both.
- One page that is slightly off-topic is worth less than the search results you
  already have; stop when the answer is stable across two independent sources.
"""

GUI_APP_AUTOMATION = """---
name: gui-app-automation
description: Operate a desktop application precisely by acting on named controls, not guessed pixels.
when_to_use: click, type into, fill in, open the app, navigate the window, use the settings, press
tools: focus_window, observe, click, type, press, key_sequence, scroll, wait
---

# Driving an app precisely

## The one rule that matters
Act on **element ids from the current observation**, never on coordinates you
worked out in your head. A plausible-looking pixel is a coin flip; an element id
resolves to that control's real centre. If you do not have a fresh observation,
get one first.

## Steps
1. `focus_window` the target app. Reading and clicking only work on the window in
   the foreground, so this is not optional when the app is behind something.
2. `observe` and read the real labels. Do not assume a control's wording from
   memory of a similar app - "Save as", "Save As" and "Save a copy" are three
   different controls.
3. Act, one step at a time: `click` a control, `type` into a field, `press` a
   hotkey. After anything that can change the screen (a click, a scroll, a new
   window, a dialog), `observe` again - the ids you had are now stale.
4. Prefer the keyboard when a shortcut exists. A hotkey is one action and cannot
   land on the wrong control.

## Verify, then claim
After acting, confirm the effect from a fresh observation before telling the user
it worked. "Clicked Save" is a claim about the button; "the title bar no longer
shows an asterisk" is evidence. If the observation contradicts the claim, say
what you saw instead.

## When a control is not found
- The name may be worded differently: re-read the labels and try the exact text
  you see.
- The window may not be focused, or a modal dialog may be on top of it.
- If the tool offers several candidates, **read them to the user and let them
  choose**. Never pick one of several plausible controls and hope - that is the
  single fastest way to do something destructive by accident.
- Do not repeat the same failing action with the same arguments. A retry that
  changes nothing tells you nothing.
"""

SYSTEM_TRIAGE = """---
name: system-triage
description: Diagnose a slow, hot, full or misbehaving machine and report what is actually wrong.
when_to_use: slow, lagging, hot, fan, disk full, memory, cpu, why is it, performance, freezing, network
tools: system_status, system_diagnostics, process_intel, net_intel, run_command
---

# Machine triage

## Steps
1. `system_status` first: CPU, memory, disk, battery, uptime. This is the cheap
   picture and it usually narrows the problem to one resource.
2. `system_diagnostics` for the deeper report when the first answer is unclear.
3. `process_intel` to find who is using the strained resource. Name the top two
   or three processes with their real numbers.
4. `net_intel` when the complaint is about connectivity or slowness that is not
   CPU, memory or disk.
5. Only then propose a fix, and say what it will cost: closing a process loses
   unsaved work, freeing disk is slower than it looks, and a reboot ends whatever
   is running.

## Reporting
- Lead with the bottleneck and its number ("memory is at 94%, and the top three
  processes are eating 6.1 of 7.7 GB").
- Distinguish cause from symptom. High CPU from a fan spinning is a symptom; a
  process in a hot loop is a cause.
- Say plainly when nothing is wrong. "Uptime is 41 days and everything is
  healthy" is a valid finding, and inventing a problem to look useful is not.
- Never kill a process, delete files, or change settings as part of *diagnosis*.
  Diagnose, report, then act only if the user asks.
"""

INBOX_TRIAGE = """---
name: inbox-triage
description: Read the user's mail and turn it into a short list of decisions, not a wall of text.
when_to_use: email, inbox, mail, unread, messages, anything important, reply to
tools: connector, memory_search, write_file, notify
---

# Inbox triage

## Steps
1. `connector` to read the account. Read only what you need for this question -
   fetching everything makes the answer later and noisier.
2. `memory_search` for context on the senders and topics that matter. A message
   from a colleague about "the file" only makes sense with that history.
3. Sort into four buckets and keep them short:
   - **Needs a decision** from the user, with the question in one line;
   - **Needs a reply**, with a one-line draft of what you would say;
   - **Waiting on someone else** - no action, just tracking;
   - **Noise**, compressed to a count.
4. Report the buckets. Write a longer summary to a file only if the user wants
   one they can keep.

## Rules
- **Never send, reply, delete, archive or mark read without being asked.** Reading
  is reversible; sending is not. Draft the reply and let the user approve it.
- Quote the actual words when a message is ambiguous instead of paraphrasing it
  into something clearer than it was.
- Do not re-state the whole inbox. If a bucket is empty, say the bucket is empty.
- Credentials for connectors live in the user's environment; never echo a secret,
  a token, or an address the user did not ask about into the transcript.
"""

LONG_FORM_DRAFTING = """---
name: long-form-drafting
description: Draft a long document with real structure, then revise it in place instead of rewriting.
when_to_use: write a document, draft, report, essay, article, documentation, proposal, edit my writing
tools: write_file, edit_file, read_document, list_dir
---

# Long-form drafting

## Steps
1. Fix the contract before writing a word, and say it back to the user for a
   correction: **audience**, **length**, **what it must achieve**, **format**.
   Half of bad long documents are a mismatch here, not bad prose.
2. Write the outline as headings only, and check it before filling it in. Each
   heading should be a claim, not a category: "Costs fall after year two" beats
   "Costs".
3. Draft section by section with `write_file`. Do not write the whole thing in
   one call - a document you cannot inspect is one you cannot fix.
4. Revise with `edit_file` so the file keeps its identity and the user can see
   the change. Re-reading with `read_document` before an edit beats trusting
   memory of what you wrote two steps ago.
5. Finish with a pass for the things that are cheap to miss: does the opening
   promise what the body delivers, does each section earn its length, and does
   the ending say what happens next.

## Rules
- Never pad to reach a length. A short document that answers the question is the
  deliverable; an inflated one is the complaint.
- Keep the user's voice and terminology when they gave you a draft to extend.
- Do not invent specifics - numbers, dates, names, quotations. Mark a gap as a
  gap and let the user fill it.
"""

#: Seeded in this order, so the index opens with the broadest usefulness first.
BUILTIN_SKILLS = (
    RESEARCH_BRIEF,
    GUI_APP_AUTOMATION,
    INBOX_TRIAGE,
    LONG_FORM_DRAFTING,
    SYSTEM_TRIAGE,
)
