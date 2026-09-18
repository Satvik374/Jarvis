---
name: inbox-triage
description: Read the user's mail and turn it into a short list of decisions, not
  a wall of text.
when_to_use: email, inbox, mail, unread, messages, anything important, reply to
tools: connector, memory_search, write_file, notify
version: '1.0'
created: '2026-09-18T14:52:34'
updated: '2026-09-18T14:52:34'
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
