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

INSTALL_APPLICATION = """---
name: install-application
description: Install a program by researching it, preferring a terminal package manager, falling back to the browser, and reporting exactly what was done.
when_to_use: install, setup, set up, download and install, get this app, add software, winget, package manager, needs installing, how do I get
tools: web_search, read_url, run_command, download_file, open_url, open_app, focus_window, click, type, press, wait_for, observe, notify, ask
---

# Installing an application

## The order that matters
Research first, terminal second, browser third. The browser is a fallback, never
the opening move: a package manager installs the right build, in one step, and
leaves a record you can check afterwards.

## Steps
1. **Research before touching anything.** `web_search` the application by name
   and read the official page with `read_url`. You need: the official source, the
   exact package id or install command, the current version, and any system
   requirement. A download link from an ad or a mirror is how adware gets in.
2. **Try the terminal first.** On Windows use `winget` (then `choco`/`scoop` if
   present); on macOS `brew`; on Linux `apt`/`dnf`/`pacman`. Run it with
   `run_command` - for example
   `winget install --id <Vendor.App> -e --accept-source-agreements`. Prefer a
   package manager over a manual download whenever one carries the program.
3. **If only a manual installer exists, ask before using the browser.** Tell the
   user the package manager does not have it and ask permission to download the
   setup file in the browser. Do not silently switch to clicking.
4. **Browser download, once you have permission.** `open_url` the OFFICIAL
   download page from step 1, find the download control, and let the file arrive.
   Note the filename and folder - it usually lands in Downloads. If the page
   offers a direct link you can trust, `download_file` is faster than clicking.
5. **Run the setup file.** `run_command` the installer path directly (the `.exe`
   or `.msi` you just downloaded). If it needs a wizard, `focus_window` it and
   step through with `click`/`type`/`press`, `wait_for`ing each new window.
   Accept the default choices unless the user asked otherwise, and decline any
   bundled extra software the wizard offers.
6. **Verify it is really installed** - do not assume. Run the program's version
   command with `run_command` (`<app> --version`), or confirm the package with
   `winget list`, before claiming success.
7. **Report back in plain language:** what was installed, which version, where it
   came from, and how to launch it. If it failed, say what failed - never claim a
   success you could not check.

## Rules
- Download from the vendor's own site or an official package repo, never a
  search-result ad, a file-sharing host, or a "crack" site.
- Never disable antivirus or dismiss a security warning to make an installer
  proceed.
- If the installer needs a reboot or admin rights you cannot obtain, stop and
  tell the user exactly what is needed.
"""

UNINSTALL_APPLICATION = """---
name: uninstall-application
description: Remove a program cleanly - confirm which one, then check that it is gone instead of leaving it half-removed.
when_to_use: uninstall, remove the app, delete program, get rid of, reinstall, clean uninstall, no longer needed
tools: run_command, open_app, focus_window, click, type, press, observe, wait_for, system_diagnostics, notify, ask
---

# Uninstalling an application

## Steps
1. **Confirm the exact program.** Names collide ("Acrobat" vs "Acrobat
   Reader"). Read the installed list first: `run_command` `winget list`, and ask
   the user to choose when more than one candidate matches.
2. **Uninstall from the terminal when possible:**
   `winget uninstall --id <Vendor.App> -e`. It removes the package without
   depending on finding the right window.
3. **Otherwise use the system uninstaller.** Open *Add or remove programs*
   (Settings > Apps > Installed apps), locate the entry, choose Uninstall, and
   step through the wizard with `click`/`press`, `wait_for`ing each window.
4. **Clean up only what you are sure about.** Leftover folders in `%APPDATA%` or
   `Program Files` may hold the user's files, not just the program's own files.
   Ask before deleting anything that is not clearly install output.
5. **Verify:** re-run `winget list` (or reopen the installed list) and show that
   the entry is gone before reporting success.
6. **Report** what was removed and anything you deliberately left behind.

## Rules
- Removing an application can take its data with it. Say so before starting when
  the user has used the program for real work.
- A closed window is not evidence of an uninstall; the installed list is.
"""

DOWNLOAD_FILE = """---
name: download-a-file
description: Fetch a file from the web, preferring a direct terminal download and falling back to the browser only when needed.
when_to_use: download, fetch, save a file, get the file, grab that file, pull down, curl, wget
tools: web_search, read_url, download_file, run_command, open_url, focus_window, click, observe, wait_for, find_files, notify
---

# Downloading a file

## Steps
1. **Get the real URL.** `read_url` or `web_search` the page to find the actual
   file link, not the page that links to it. Saving a page URL as a file gives a
   small HTML stub, not the thing the user wanted.
2. **Download directly first.** Use the `download_file` action, or `run_command`
   with `curl -L -o <path> <url>` / `Invoke-WebRequest -Uri <url> -OutFile
   <path>`. Direct downloads do not need a browser and leave a known path.
3. **Use the browser only when the download is gated** behind a login, a
   CAPTCHA, or a "click to continue" page. There, `open_url` the page, click the
   download control, and watch the browser's download shelf for the finished
   file.
4. **Confirm the file actually arrived.** `find_files` for the expected name in
   Downloads, and check that the size is not zero and the extension is right. A
   redirect page saved as `.pdf` is a classic silent failure.
5. **Report** the full path and size so the user can open it.

## Rules
- Do not rename or move the file unless asked; the user expects it where their
  downloads normally go.
- If the site asks for credentials you do not have, stop and ask the user rather
  than guessing.
"""

SETUP_DEV_ENVIRONMENT = """---
name: setup-dev-environment
description: Get a code project running from a fresh checkout by reading its manifest, installing dependencies, and proving it starts.
when_to_use: set up the project, install dependencies, npm install, pip install, requirements, virtualenv, venv, clone and run, dev environment, build the project
tools: list_dir, read_file, run_command, python, session_exec, write_file, find_files, notify
---

# Setting up a development environment

## Steps
1. **Read before installing.** `list_dir` the project root and `read_file` its
   manifest(s): `requirements.txt`/`pyproject.toml`, `package.json`,
   `Cargo.toml`, `go.mod`, or the README's setup section. The manifest names the
   dependencies and the run command; guessing does not.
2. **Match the runtime the project expects.** Check the manifest's version pins
   against `python --version` / `node --version`. Do not install a new language
   runtime unless the project needs one and the user agrees.
3. **Install into an isolated environment.** For Python create a virtualenv
   (`python -m venv .venv`) and install into it; for Node run the project's
   package manager and prefer the lockfile (`npm ci` over `npm install`). Keep
   the system environment untouched.
4. **Run the project the way its README says,** then run its tests if it has any.
   A setup that installs cleanly but cannot start is not finished.
5. **Record what worked.** `write_file` a short `SETUP_NOTES.md` (or append to
   the README) with the exact commands, so the next session is a lookup.
6. **Report** the start command and any environment variable still needed.

## Rules
- Prefer lockfile installs over loose upgrades; an unpinned install today is a
  broken build tomorrow.
- If a dependency fails to build, report the real error instead of quietly
  loosening a version pin.
"""

ORGANIZE_FILES = """---
name: organize-files
description: Tidy a folder by sorting files into clear categories, proposing the plan before moving anything.
when_to_use: organize, tidy, clean up, sort files, downloads folder, desktop clutter, rename files, file mess
tools: list_dir, find_files, make_dir, move_file, copy_file, read_file, notify, ask
---

# Organizing a folder

## Steps
1. **Look before you move.** `list_dir` the target folder and read a sample of
   names to learn the real categories in this folder - "invoices" and "receipts"
   may be one job or two.
2. **Propose a short plan** and get it approved when the folder is not obvious
   junk: the target folders, roughly how many files each receives, and what
   happens to the leftovers. A tidy-up nobody agreed to is just a move nobody can
   find anything in afterwards.
3. **Create the folders** with `make_dir`, then `move_file` one category at a
   time, so a mistake is one category to undo.
4. **Never delete.** Unmatched files go into an `_unsorted` folder; the user
   decides their fate. Deleting to make a folder look tidy is a data-loss bug.
5. **Report** the counts moved per folder and name anything left unsorted.

## Rules
- Preserve file extensions and any dates in the name; a rename that loses the
  date makes a sorted folder worse than the original.
- If two files would collide at the destination, keep both - add a suffix rather
  than overwriting one with the other.
"""

BACKUP_FILES = """---
name: backup-files
description: Make a dated archive of chosen files, verify it, and tell the user exactly where it is.
when_to_use: backup, archive, zip, compress, save a copy, copy my files, snapshot
tools: list_dir, find_files, run_command, copy_file, write_file, notify, ask
---

# Backing up files

## Steps
1. **Agree on scope and destination.** Which folder(s), and where the backup
   should land (another drive, a network share, a dated folder). A backup on the
   same disk as the original does not survive the failure it is meant for; say so
   if that is the only option.
2. **Make a dated, self-describing archive:** `run_command` something like
   `Compress-Archive -Path <folder> -DestinationPath <dest>/backup-YYYY-MM-DD.zip`
   (or `tar -czf`). The date in the name lets a folder of backups sort itself.
3. **Verify the archive** - do not trust the exit code alone. Check the file
   exists, its size is plausible for the source, and ideally that you can list
   its contents back.
4. **Report** the full path, the size, and what was included or skipped.

## Rules
- Never overwrite an existing backup. A second backup to the same name destroys
  the first one silently.
- If the source contains credentials or keys, say so before copying them to a
  destination the user did not specifically name.
"""

SEND_EMAIL = """---
name: send-email
description: Draft an email in the user's voice, show it for approval, and only then send it.
when_to_use: send an email, write an email, reply to, draft a message, email someone, mail them, follow up by email
tools: connector, memory_search, notify, ask
---

# Sending an email

## Steps
1. **Find the thread and the recipient.** Use `connector` to read the relevant
   messages, and `memory_search` for how the user writes to this person. Reply
   in-thread when there is one; a fresh subject line loses the context the
   recipient needs.
2. **Draft it to the user, never straight to the world.** Show the recipient,
   the subject and the full body, and ask for approval. Sending is the one action
   here that cannot be taken back.
3. **Send only after an explicit yes** (or the user's original message said "send
   it"). Quote the final text back so what is approved is what is sent.
4. **Confirm** what was sent and to whom, and keep the tone the user uses with
   that recipient.

## Rules
- Never send, forward, or reply without approval. Reading is reversible; sending
  is not.
- Do not invent commitments, prices, dates, or attachments the user did not
  mention.
- Never move money or share credentials or personal data over email on your own
  initiative.
"""

UPDATE_SOFTWARE = """---
name: update-software
description: Update the operating system or an application, and report what changed without pretending a pending reboot is done.
when_to_use: update, upgrade, windows update, patch, latest version, outdated, security updates
tools: run_command, system_status, open_app, focus_window, click, press, observe, wait_for, notify, ask
---

# Updating software

## Steps
1. **Say what will update** and confirm it: the whole OS, a specific app, or the
   package set. "Update everything" can restart the machine or sign the user out.
2. **Prefer the terminal.** `run_command` `winget upgrade --all
   --accept-source-agreements --accept-package-agreements` for apps; use the OS
   updater (Settings > Windows Update) for system patches; the distro's package
   manager on Linux.
3. **If a wizard is needed,** `focus_window` it and step through,
   `wait_for`ing each progress window rather than clicking into a frozen one.
4. **Wait for real completion.** Updates download, then install, then ask to
   restart. A pending reboot is not the same as updated - report it as pending
   and let the user choose when to restart.
5. **Report** what was updated, what version it moved to, and whether a restart
   is still outstanding.

## Rules
- Never restart or sign out the machine without the user's go-ahead.
- Do not apply driver or firmware updates unprompted; a bad driver update is far
  harder to undo than a missed patch.
"""

FILL_WEB_FORM = """---
name: fill-a-web-form
description: Complete an online form accurately, and never submit a payment or legal agreement without approval.
when_to_use: fill out, form, sign up, register, book, reservation, checkout, apply online, enter my details
tools: open_url, browser_action, read_url, click, type, press, observe, wait_for, ask, notify
---

# Filling a web form

## Steps
1. **Read the whole form before typing.** `open_url` the page and read it, or
   `browser_action` to snapshot it. Knowing the fields up front prevents the
   half-filled form submitted by accident.
2. **Gather the answers from the user or known memory.** `memory_search` for
   saved details, and ask the user for anything you genuinely do not have. Do not
   invent a birth date, an address, or a payment detail.
3. **Fill field by field,** typing only into the field you just focused, and
   re-reading after anything that adds a section (a change that reveals ten more
   fields). Watch for validation errors and fix them.
4. **Stop at the submit line** when the form takes money, accepts terms, files a
   legal document, or cannot be undone. Show the user the completed form and the
   price/terms, and let them press submit or tell you to.
5. **Confirm** the reference number or the confirmation screen before reporting
   success.

## Rules
- Never enter payment details or agree to terms on the user's behalf.
- Never type credentials into a page reached from an ad or an untrusted link; go
  to the official site by URL.
- If a CAPTCHA blocks the form, tell the user it needs their hand - do not try
  to defeat it.
"""

TROUBLESHOOT_CONNECTION = """---
name: troubleshoot-connection
description: Find out why the network is not working before changing anything, then fix the one real cause.
when_to_use: internet, wifi, connection, network, online, cannot connect, dns, no internet, pages will not load
tools: net_intel, system_status, run_command, http_request, notify
---

# Fixing a connection

## Steps
1. **Establish what fails and what works.** `net_intel` for interfaces, addresses
   and routes; `system_status` for whether the adapter is up. Is it one site, all
   of the web, or the whole network?
2. **Test DNS and reachability separately:** resolve a name and ping a literal
   address with `run_command` (`nslookup <host>`, `ping 1.1.1.1`). If the literal
   IP works and names do not, it is DNS, not the connection.
3. **Fix the one cause you found, and only that:** renew the address, cycle the
   adapter, flush DNS. Do not stack changes - if three things change at once, you
   learn nothing about which one fixed it.
4. **Re-test the original failure,** not just the layer you touched. A fixed
   adapter is not a fixed connection until the page loads.
5. **Report** the cause in plain words and what you changed; if it is the ISP or
   the router's own problem, say that instead of hunting on the machine.

## Rules
- Do not change network settings, firewall rules, or DNS servers on a hunch;
  make one change with a reason, then test.
- Fixing connectivity must not weaken security (never disable the firewall).
"""

MEDIA_PLAYBACK = """---
name: media-playback
description: Play, pause and control media, using the system media controls rather than hunting for pixels on a video.
when_to_use: play, pause, music, song, video, volume, mute, next track, media, spotify, youtube
tools: media, media_intel, open_app, open_url, click, observe, press, notify
---

# Controlling media

## Steps
1. **Find what is playing first.** `media_intel` names the current session and its
   state, so "pause it" acts on the thing actually playing instead of guessing an
   app.
2. **Prefer the media action** over clicking a player window: `media` sends the
   system play/pause/next/previous/volume command and works even when the player
   is minimised or behind another window.
3. **To start something new,** `open_url` the page (a video or a track) or
   `open_app` the player, `wait_for` the window, then use the keyboard (space for
   play/pause) rather than aiming at a small control.
4. **Confirm the state changed** - the media session's state is the evidence, not
   the fact that you pressed a key.
5. **Report** what is playing and the volume when the user asked for a change.

## Rules
- Do not raise the volume sharply; a sudden blare is worse than a wrong track.
- Do not add to a playlist, buy, or download media the user did not request.
"""

DATA_ENTRY = """---
name: data-entry
description: Get information into a spreadsheet or table accurately, checking the row count before and after.
when_to_use: spreadsheet, excel, csv, enter data, fill the sheet, table, log, record entries, tabulate
tools: read_file, read_document, write_file, edit_file, python, run_command, open_app, click, type, observe, notify
---

# Data entry

## Steps
1. **Learn the shape first.** `read_file` the existing sheet or CSV and read its
   header row. New rows must match the existing columns exactly, blank cells and
   all.
2. **Prefer a script over keystrokes.** For anything more than a few rows, use
   `python` or `run_command` to append/transform the file (or `edit_file` the CSV
   directly): it is faster and byte-exact. Reserve clicking and typing for an app
   that has no file behind it.
3. **Work on a copy or a new column** when the source data matters, and only
   replace the original once the result checks out.
4. **Verify with a count and a sample.** Rows before vs after, and re-read the
   first and last new row. The inserted row count is the cheapest proof that
   nothing was dropped.
5. **Report** how many rows were added and to which file/sheet.

## Rules
- Never overwrite a source file in place without a copy; a bad transform on the
  only copy is unrecoverable.
- Keep the original formatting and column order; do not "tidy" a sheet the user
  will hand to someone else.
"""

#: Seeded in this order, so the index opens with the broadest usefulness first.
BUILTIN_SKILLS = (
    RESEARCH_BRIEF,
    GUI_APP_AUTOMATION,
    INSTALL_APPLICATION,
    SETUP_DEV_ENVIRONMENT,
    INBOX_TRIAGE,
    LONG_FORM_DRAFTING,
    SEND_EMAIL,
    FILL_WEB_FORM,
    ORGANIZE_FILES,
    BACKUP_FILES,
    DOWNLOAD_FILE,
    UPDATE_SOFTWARE,
    SYSTEM_TRIAGE,
    TROUBLESHOOT_CONNECTION,
    MEDIA_PLAYBACK,
    DATA_ENTRY,
    UNINSTALL_APPLICATION,
)
