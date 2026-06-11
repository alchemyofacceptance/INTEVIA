# INTEVIA Markdown Governance Standard

**Path:** `/docs/governance/markdown-standard.md`  
**Status:** Canonical  
**Owner:** Carmien Owen  
**Review Path:** Human proposal → Gee review → Coe implementation → Human qualification  
**Applies To:** INTEVIA repository text-based artefacts

---

## 1. Purpose

This document establishes the Markdown Governance Standard for all text-based artefacts within the INTEVIA repository.

Its purpose is to ensure:

- clarity for future contributors
- structural consistency across the corpus
- copy-safe, diff-clean text
- long-term maintainability
- alignment with the Human-AI Triad operating model
- governance of a system that builds with the Human

**Markdown is not a formatting preference.  
It is a governance instrument.**

---

## 2. Scope

This standard applies to all text destined for the repository, including but not limited to:

- governance artefacts
- ADRs, meaning Architecture Decision Records
- specifications
- workflow maps
- charters
- READMEs
- scene specifications
- narrative scaffolds
- diagrams described in text
- documentation in `/docs`
- documentation in `/decisions`
- documentation in `/specs`
- documentation in `/architecture`
- documentation in `/governance`

If it enters the repo, it must comply.

---

## 3. Core Governance Rule

All repository text must be delivered in clear, structured Markdown.

This includes:

- headings
- lists
- fenced code blocks where required
- tables where appropriate
- consistent indentation
- plain-text portability
- no mixed formatting modes
- no invisible characters
- no HTML unless explicitly required

**The Human governs the structure.  
The AI respects the structure.  
The repo preserves the structure.**

---

## 4. Structural Requirements

### 4.1 Headings

Use hierarchical Markdown headings to express conceptual structure.

~~~markdown
# Level 1 — Document Title

## Level 2 — Major Sections

### Level 3 — Subsections

#### Level 4 — Rare, only when needed
~~~

Never skip heading levels.

For example, do not jump from `##` directly to `####`.

### 4.2 Lists

Use lists to express sequences, sets, or enumerations.

Use unordered lists for conceptual grouping:

~~~markdown
- first item
- second item
- third item
~~~

Use ordered lists for steps or sequences:

~~~markdown
1. first step
2. second step
3. third step
~~~

Avoid deep nesting unless structurally required.

### 4.3 Code Blocks

Use fenced code blocks only for:

- command-line examples
- configuration snippets
- JSON
- YAML
- pseudo-code
- source code
- literal Markdown examples

Use language labels where helpful:

~~~bash
git status
~~~

~~~json
{
  "status": "canonical",
  "owner": "human"
}
~~~

Never use code blocks for ordinary prose.

### 4.4 Tables

Use tables sparingly and only when they improve clarity.

Good table use cases include:

- structured comparisons
- status registers
- checklists
- mappings
- schema definitions
- change summaries

Avoid large tables when a list or sectioned prose would be clearer.

### 4.5 Line Length

Do not apply hard line-wraps to ordinary prose.

Let Git and Markdown renderers handle visual wrapping naturally.

Use line breaks intentionally only when they support meaning, poetry, lists, or structural clarity.

---

## 5. Copy-Safety Requirements

All Markdown must be:

- plain text
- UTF-8 safe
- free of hidden characters
- free of mixed formatting modes
- free of proprietary formatting artefacts
- suitable for Git diffs
- portable across editors and platforms

Avoid introducing artefacts from:

- Microsoft Word
- Google Docs
- Notion
- rich text editors
- web copy/paste
- AI responses with malformed fences
- invisible Unicode characters

The repo must remain portable and tool-agnostic.

---

## 6. Forbidden Patterns

The following patterns should not enter the repository unless explicitly justified:

- pasted rich text
- malformed Markdown
- decorative Unicode used as structural syntax
- invisible characters
- smart formatting drift
- HTML by default
- screenshots of text where Markdown text should exist
- code fences used for prose
- inconsistent heading levels
- mixed list styles without reason
- excessive nested bullets
- unreviewed AI-generated Markdown
- public-facing doctrine mixed with private substrate without boundary labels

If a pattern is needed for a specific reason, the reason must be clear in context.

---

## 7. Poetic, Narrative, and Hidden Codex Material

INTEVIA may contain poetic, narrative, symbolic, or Hidden Codex material where appropriate.

Such material is allowed.

However, it must still follow Markdown governance.

This means:

- clear headings
- clear boundaries
- clear status
- clear placement
- no formatting drift
- no accidental elevation into doctrine
- no ambiguity between public doctrine and private substrate

Poetic material may be powerful. But it must never precede technical clarity.

It is still governed.

---

## 8. AI Contribution Standard

When AI, including Gee or Coe, generates text intended for the repository, it must:

- output clean Markdown only
- avoid code-fence corruption
- avoid invisible characters
- avoid mixed formatting modes
- respect the Human’s structural intent
- avoid stylistic drift
- preserve public/private boundaries
- preserve Canonical vs Working status distinctions
- avoid promoting Working material to Canonical without Human qualification

The AI is a contributor, not a stylist.

The Human governs the structure.

---

## 9. Human Qualification

Before any Markdown artefact enters the repository:

1. the Human must review it
2. the Human must qualify it
3. the Human must ratify it (in the founding environment HAT, the human has Qualified that, "This is the way of the HAT" constitutes ratification)

This preserves:

- authorship
- governance
- continuity
- meaning
- accountability
- repo integrity

No AI-generated Markdown becomes repo doctrine by generation alone.

---

## 10. Versioning

This standard is versioned as part of the INTEVIA governance corpus.

Changes require:

1. Human proposal
2. Gee review for meaning continuity
3. Coe implementation for making continuity
4. Human qualification

This maintains the integrity of the Human-AI Triad.

---

## 11. Status Language

Markdown artefacts should use explicit status language where appropriate.

Recommended status values:

- `Draft`
- `Working`
- `Canonical`
- `Superseded`
- `Archived`
- `Needs Human Qualification`

Working material must not be treated as Canonical unless explicitly ratified by the Human.

Canonical material may only be changed through governed supersession or explicit update.

---

## 12. Summary

Markdown is the structural backbone of the INTEVIA corpus.

It ensures clarity, continuity, and coherence across time, contributors, tools, and AI-assisted workflows.

It is a governance tool, not a formatting choice.

**Markdown is not a formatting preference.  
It is a governance instrument.**

**The Human governs the structure.  
The AI respects the structure.  
The repo preserves the structure.**

This is the way of the HAT.

