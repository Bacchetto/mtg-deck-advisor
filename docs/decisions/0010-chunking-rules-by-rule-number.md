# 0010 - Chunking the Comprehensive Rules by rule number

**Status:** Accepted, 2026-10-02. Revised the same day after the retrieval eval: the embedded context was dropped.
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
- **Each rule is embedded as its own text**, examples included, on one line.
- **No added context (revised after measuring).** The first design added the section heading and the parent rule's first sentence to the embedded text, because a lettered rule often doesn't say what it's about:

  ```
  903. Commander. 903.4: The Commander variant uses color identity to determine what cards can be in a deck with a certain commander. 903.4c: Reminder text is ignored when determining a card's color identity. See rule 207.2.
  ```

  The retrieval eval measured it as a loss on the 45 owner-reviewed rules questions:

  | Embedded text | recall@10 | MRR@10 |
  |---|---|---|
  | The rule's own text | **0.97** | **0.71** |
  | With section and parent's first sentence | 0.91 | 0.66 |

  The results are from vector search ([report](../../evals/reports/2026-10-02-retrieval.md)); hybrid search showed the same direction. A plausible reason: every rule in a section shares its heading, and every subrule shares its parent's sentence, which pulls their vectors together and blurs what distinguishes them. So the context went, as this ADR said it would if it didn't help.
- **Staleness comes from the rule's own content hash**, as for cards.
- **The glossary is left out.** Most glossary entries restate or point to a numbered rule ("See rule 701.33"). Retrieving one would give an answer with no rule number to cite. Leaving it out keeps every chunk citable.

## Alternatives

**Fixed-size windows (for example 500 tokens, overlapping).** This is the usual default for unstructured text, and wrong for this document:
- A window cuts rules in half, and one window holds several rules, so a citation can't name the rule that answered.
- The overlap repeats text across chunks to make up for boundaries this document already provides.

**Whole sections (903 as one chunk).** This gives too much per chunk. Section 903 runs to dozens of rules, so a question about commander damage would retrieve all of Commander, and the citation would be "903" instead of "903.10a". Embedding a long chunk also averages its many topics into one vector.

**A rule together with all its subrules.** This would give context, but at a cost:
- 903.4 with 903.4a–f is already 1,500 characters, and some rules have twenty or more subrules.
- The citation would be less precise.

**Single rules with added context** (section heading and parent's first sentence, the first design). It keeps single-rule chunks while giving subrules their topic, but it measured worse than no context (see above).

## Consequences

- Every retrieved chunk is a citable rule number, which is what RAG-3's citations and RAG-4's "not found" check need.
- The chunks are short (a mean of about 60 tokens), so the embedding model never truncates, and a long rule is still a single chunk: the longest is 2,872 characters, far inside the model's 32k-token limit.
- Embedding all 3,166 rules takes 43 s locally; a rerun with nothing changed takes 0.1 s.
- **Retrieval quality, measured:** with vector search (the rules default; hybrid scored 0.74 recall@10), the answering rule is in the top 10 for 44 of the 45 eval questions. The one miss, "can I run more than one copy of a card?", is answered by 903.5b, which says "a different English name" and never "copy". That kind of vocabulary gap is the case for a reranker or a model-written answer that can cite related rules (Milestone 5).
