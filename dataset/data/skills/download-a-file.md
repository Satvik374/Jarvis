---
name: download-a-file
description: Fetch a file from the web, preferring a direct terminal download and
  falling back to the browser only when needed.
when_to_use: download, fetch, save a file, get the file, grab that file, pull down,
  curl, wget
tools: web_search, read_url, download_file, run_command, open_url, focus_window, click,
  observe, wait_for, find_files, notify
version: '1.0'
created: '2026-09-26T21:39:29'
updated: '2026-09-26T21:39:29'
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
