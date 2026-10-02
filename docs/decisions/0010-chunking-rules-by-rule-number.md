# 0010 - Chunking the Comprehensive Rules by rule number

**Status:** Accepted, 2026-10-02
**Applies to:** `mtg_deck_advisor.retrieval.text.rule_text`, `retrieval.embeddings.embed_rules`, the `rules` and `rule_embeddings` tables

## Context

RAG-2 asks for long documents to be chunked, embedded and retrievable, with the chunking strategy justified. RAG-3 asks for answers that cite their source. The long document here is the Comprehensive Rules: about 3,200 numbered rules in a strict hierarchy:

- a section (`903. Commander`)
- a rule (`903.4`)
- lettered subrules (`903.4a` to `903.4f`)
- examples under any of them

Judges, players and the rules themselves cite by rule number ("see rule 903.4c").

From the current file (effective September 25, 2026):

| Measure | Value |
|---|---|
| Rules | 3,166, of which 1,991 are lettered subrules |
| Mean length | 252 characters (about 60 tokens) |
| 95th percentile | 668 characters |
| Longest | 205.3m, the creature type list, at 2,872 characters |

## Decision

- **One chunk per numbered rule, examples included.** The ingestion parser (Milestone 2) already stores rules this way, one row per rule, and the embedding adds nothing on top of that. The rule number is the chunk's ID and its citation.
- **The embedded text adds context; the chunk doesn't change.** A lettered rule often doesn't say what it's about. "Reminder text is ignored when determining a card's color identity" is clear, but many subrules start "If..." or "That player...". So the embedded text is built like this:

  ```
  903. Commander. 903.4: The Commander variant uses color identity to determine what cards can be in a deck with a certain commander. 903.4c: Reminder text is ignored when determining a card's color identity. See rule 207.2.
  ```

  It's the section heading, then the parent rule's **first sentence** only (the topic, without the parent's full text drowning out the subrule), then the rule itself. What's retrieved, shown and cited is still the single rule.
- **The stored hash covers the whole embedded text**, not just the rule's own text. Changing a parent rule re-embeds its subrules too, because their context changed.
- **The glossary is left out.** Most glossary entries restate or point to a numbered rule ("See rule 701.33"). Retrieving one would give an answer with no rule number to cite. Leaving it out keeps every chunk citable.

## Alternatives

**Fixed-size windows (for example 500 tokens, overlapping).** This is the usual default for unstructured text, and wrong for this document:
- A window cuts rules in half, and one window holds several rules, so a citation can't name the rule that answered.
- The overlap repeats text across chunks to make up for boundaries this document already provides.

**Whole sections (903 as one chunk).** This gives too much per chunk. Section 903 runs to dozens of rules, so a question about commander damage would retrieve all of Commander, and the citation would be "903" instead of "903.10a". Embedding a long chunk also averages its many topics into one vector.

**A rule together with all its subrules.** This would give context, but at a cost:
- 903.4 with 903.4a–f is already 1,500 characters, and some rules have twenty or more subrules.
- The citation would be less precise.

The parent's first sentence gets most of the context benefit while keeping single-rule chunks.

**No context (the rule's text alone).** This is the simplest option, and the baseline for the comparison below.

## Consequences

- Every retrieved chunk is a citable rule number, which is what RAG-3's citations and RAG-4's "not found" check need.
- The chunks are short (a mean of about 60 tokens), so the embedding model never truncates, and a long rule is still a single chunk: the longest embedded text is under 3,000 characters, far inside the model's 32k-token limit.
- Embedding all 3,166 rules takes 43 s locally; a rerun with nothing changed takes 0.1 s.
- **The context choice is measured, not assumed.** The retrieval eval (issue #64) compares rule text with context against rule text alone, on the 45 owner-reviewed rules questions. If the context doesn't help, it goes; this ADR will be updated with the result either way.
