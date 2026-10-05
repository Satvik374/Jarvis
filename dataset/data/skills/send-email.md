---
name: send-email
description: Draft an email in the user's voice, show it for approval, and only then
  send it.
when_to_use: send an email, write an email, reply to, draft a message, email someone,
  mail them, follow up by email
tools: connector, memory_search, notify, ask
version: '1.0'
created: '2026-09-26T21:39:29'
updated: '2026-09-26T21:39:29'
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
