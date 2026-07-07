# GOVERNANCE_INDEX.md
# Root-level inspection aid for INTEVIA governance artefacts.
# This index is a declared inspection aid, not authority.
# Its claims are only as good as their verification method — see below.

## How to read this index

Each entry below documents ONE artefact using this schema:

Artifact:            <name>
Path:                <repo-relative path>
Purpose:             <one line — what this artefact is for>
Status:              <draft | candidate | ratified | deprecated | superseded | empty>
Populated:           <yes | no | partial>
Last verified:       <date>
Verified by:         <author-self-declared | cross-node | human-governor | ci-automated | third-party | unavailable>
Verification method: <manual read | diff-against-source | automated check | hash match | unavailable>
Review basis:        <system | source-backed packet | curated presentation | inaccessible>
Hash / commit ref:   <sha or "unavailable">
Authority tier:      <operational | governance | constitutional>
Verification limitations: <one line describing what could not be verified>
Notes / exclusions:  <anything a reviewer should know before trusting this entry>

## Index rules

* The index does not confer authority.
* The index does not ratify artefacts.
* The index does not prove canon status.
* Self-verification is permitted only when explicitly labelled as self-verification.
* Self-verification must never be presented as independent verification.
* Path existence, population, currency, verification method, and authority status must be tracked separately.
* Updates to path, population, or verification fields may be operational updates when evidence-backed.
* Changes to authority tier, ratification status, or canon status require Human Governor review.
* Changes to the index schema, index rules, or update-tiering require CRP handling.
* The index may lag reality; `Last verified` exists to make currency visible.
* “Claude said so” is not authority.
* Session-bound findings require re-verification before future reliance.

## Initial entries

Artifact: README.md
Path: README.md
Purpose: Root-level public orientation file for the INTEVIA repository.
Status: draft
Populated: yes
Last verified: 2026-07-07
Verified by: author-self-declared
Verification method: manual read
Review basis: system
Hash / commit ref: unavailable
Authority tier: operational
Verification limitations: Entry records root-level presence and population only; it does not verify all claims contained in README.md.
Notes / exclusions: Index entry does not confer authority or ratification.

Artifact: ROADMAP.md
Path: ROADMAP.md
Purpose: Placeholder for future public roadmap material.
Status: empty
Populated: no
Last verified: 2026-07-07
Verified by: author-self-declared
Verification method: manual read
Review basis: system
Hash / commit ref: unavailable
Authority tier: operational
Verification limitations: File is intentionally marked as not yet populated / pending publication.
Notes / exclusions: Must not be read as evidence of a populated roadmap.

Artifact: CHANGELOG.md
Path: CHANGELOG.md
Purpose: Root-level change log for visible repository changes.
Status: draft
Populated: partial
Last verified: 2026-07-07
Verified by: author-self-declared
Verification method: manual read
Review basis: system
Hash / commit ref: unavailable
Authority tier: operational
Verification limitations: Entry confirms root-level changelog presence only; it does not verify referenced deeper corpus or governance artefacts.
Notes / exclusions: A label is not evidence of the mechanism.

## Candidate entries pending path verification

These entries are not yet indexed because exact paths and verification status have not been confirmed.

Candidate: CRP source or CRP summary artefact
Reason pending: exact repo-relative path and verification status not confirmed in this mutation pass.

Candidate: evidence / corpus / memory-related artefact
Reason pending: exact repo-relative path and verification status not confirmed in this mutation pass.
