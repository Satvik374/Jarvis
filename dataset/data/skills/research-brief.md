---
name: research-brief
description: Research a question across the web and produce a short brief with real
  sources.
when_to_use: research, look up, find out, compare, investigate, sources, cite, what
  is the latest
tools: web_search, read_url, extract_web_data, write_file
version: '1.0'
created: '2026-09-18T14:52:34'
updated: '2026-09-18T14:52:34'
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
