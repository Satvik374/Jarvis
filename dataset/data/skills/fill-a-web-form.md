---
name: fill-a-web-form
description: Complete an online form accurately, and never submit a payment or legal
  agreement without approval.
when_to_use: fill out, form, sign up, register, book, reservation, checkout, apply
  online, enter my details
tools: open_url, browser_action, read_url, click, type, press, observe, wait_for,
  ask, notify
version: '1.0'
created: '2026-09-26T21:39:29'
updated: '2026-09-26T21:39:29'
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
