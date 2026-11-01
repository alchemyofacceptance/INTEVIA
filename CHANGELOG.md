# INTEVIA — Selected Repository Change Record

This is a factual navigation aid, not a release history, lifecycle ledger, or substitute for commit, Datacron, execution, or Human-authority evidence. Entries are selective and source-linked. Absence from this file does not imply that an event did not occur.

## Unreleased

INTEVIA is pre-release; this section collects notable changes until the first release. The current account reaches main `f829d1b2b714cb282d96a273fbd0887eac9c3522` on 1 November 2026.

The current development practice is **IDOPT (formerly IDOP)** — INTEVIA Development Operating Protocol and Thinking. Historical records and protocol filenames retain IDOP.

The [PKT-B record](docs/holocron/datacrons/D-S015-PKT-B-GOVERNED-ORGANISATIONS-MEMBERSHIP-AND-OFFICES.md) covers the completed organisation foundations. There is no complete website journey or public-use approval; custom offices remain held. The next work is the offices safeguard unit before office user access, followed by the test-value review. The current GitHub checks use clone mode and the six test admission rules (TAR): 126 product tests plus 19 deliberate-defect checks. The old suite of about 1,800 tests is archived and not run.

### Added

- SR-H adds database safeguards against outdated office appointments, changes to reserved offices, retirement of board offices, changes of office kind and later definitions that do not supersede the latest version; damaged consent receipts are identified as record faults.

- Organisation membership and application routes, including recorded decisions, admission and leaving an organisation; see the [packet record](docs/holocron/datacrons/D-S015-PKT-B-GOVERNED-ORGANISATIONS-MEMBERSHIP-AND-OFFICES.md) and [a721b3db](https://github.com/alchemyofacceptance/INTEVIA/commit/a721b3db4135e1885258fbb080cc5017b023db55), [ea4dcc7c](https://github.com/alchemyofacceptance/INTEVIA/commit/ea4dcc7c4e00382a4107af49494f0cd5ea4c411e), [a9dadb5e](https://github.com/alchemyofacceptance/INTEVIA/commit/a9dadb5e75106eb5b918a99d65e239526b166243).
- Governed founding, registration of a Learner (INTEVIA's person account, not course enrolment), explicit Birthing and governance formation; see the [packet record](docs/holocron/datacrons/D-S015-PKT-B-GOVERNED-ORGANISATIONS-MEMBERSHIP-AND-OFFICES.md) and [bb94c5c4](https://github.com/alchemyofacceptance/INTEVIA/commit/bb94c5c478332b5ae4ef9fd3ff1186f7765e4ba5), [10662970](https://github.com/alchemyofacceptance/INTEVIA/commit/1066297013871537ef106a23cd1d73b6d3d42f2c), [26093aef](https://github.com/alchemyofacceptance/INTEVIA/commit/26093aefc0b679dbf17e5cbb857f39e804d0ae31), [c68efab7](https://github.com/alchemyofacceptance/INTEVIA/commit/c68efab77de0f90804c38c5b1b3fa0d27f43d4e1).
- Complimentary beta access and the entitlement foundation, with unknown usage shown as unavailable; see the [packet record](docs/holocron/datacrons/D-S015-PKT-B-GOVERNED-ORGANISATIONS-MEMBERSHIP-AND-OFFICES.md) and [ac7aa844](https://github.com/alchemyofacceptance/INTEVIA/commit/ac7aa8449fc5600bc0eeb12fed82792bd5cf0455), [c71d7552](https://github.com/alchemyofacceptance/INTEVIA/commit/c71d755257c8e0c36c6671f2d1352208a0fc2dcf).
- Recorded office definitions, broader appointment and consent safeguards, and non-Coordinator office commands. All office user access waits for the SR-H database safeguards; custom offices also wait for entitlement-lifecycle assurance; see the [packet record](docs/holocron/datacrons/D-S015-PKT-B-GOVERNED-ORGANISATIONS-MEMBERSHIP-AND-OFFICES.md) and [2e295bff](https://github.com/alchemyofacceptance/INTEVIA/commit/2e295bff5170d342df8f97b5512540705ac27424), [9e5f706d](https://github.com/alchemyofacceptance/INTEVIA/commit/9e5f706dd2dc9201a70874b7e8ffc2610a050571), [52f802c7](https://github.com/alchemyofacceptance/INTEVIA/commit/52f802c794919cdaf4e2182ff561293c9a879944), [f829d1b2](https://github.com/alchemyofacceptance/INTEVIA/commit/f829d1b2b714cb282d96a273fbd0887eac9c3522).

### Changed

- Requests are recorded before their governed commands run; retries use that record and receipt time. See the [packet record](docs/holocron/datacrons/D-S015-PKT-B-GOVERNED-ORGANISATIONS-MEMBERSHIP-AND-OFFICES.md) and [e832273f](https://github.com/alchemyofacceptance/INTEVIA/commit/e832273fad9ef5785437ae344ba1970588acccf9), [de7f6e7a](https://github.com/alchemyofacceptance/INTEVIA/commit/de7f6e7a7836e52f3fa3496ac2237146f090e9c6), [e155bf25](https://github.com/alchemyofacceptance/INTEVIA/commit/e155bf25ec7e0e97145a76ac50ce7efb7d12a893).
- Continuous integration (CI), the automatic GitHub verification route, became the default closure gate. It now reuses prepared database copies in clone mode; see the [packet record](docs/holocron/datacrons/D-S015-PKT-B-GOVERNED-ORGANISATIONS-MEMBERSHIP-AND-OFFICES.md) and [39986973](https://github.com/alchemyofacceptance/INTEVIA/commit/39986973186fe43fc2baa2abfe7e8d54da7c1225), [04e9df6c](https://github.com/alchemyofacceptance/INTEVIA/commit/04e9df6ce46f0f4159c224e647844c475b862a71).
- The Human Governor set down the old test suite and commissioned a smaller suite built around explicit obligations. The archive remains in the repository; see the [packet record](docs/holocron/datacrons/D-S015-PKT-B-GOVERNED-ORGANISATIONS-MEMBERSHIP-AND-OFFICES.md) and [35c37a44](https://github.com/alchemyofacceptance/INTEVIA/commit/35c37a4443246e66cd4b3354accd6378a6cf65d3), [c50a5585](https://github.com/alchemyofacceptance/INTEVIA/commit/c50a5585fa01928301f2be61606835aebaa404d6).

### Fixed

- Departure and consent-withdrawal handling while appointments wait, database-clock usage, and shared locking and retry checks; see the [packet record](docs/holocron/datacrons/D-S015-PKT-B-GOVERNED-ORGANISATIONS-MEMBERSHIP-AND-OFFICES.md) and [772e5a5a](https://github.com/alchemyofacceptance/INTEVIA/commit/772e5a5ab77c339951d4bca23e9c126781a91533), [e9136b3b](https://github.com/alchemyofacceptance/INTEVIA/commit/e9136b3bd6ba4f6624471babc2d9e406549dd750), [f770ec67](https://github.com/alchemyofacceptance/INTEVIA/commit/f770ec67bf924ca0ba2e065d0a90a93ea3540f3d), [567860b2](https://github.com/alchemyofacceptance/INTEVIA/commit/567860b27c7c65c482a79ed41597eaba4f58c3d5), [b6fc705a](https://github.com/alchemyofacceptance/INTEVIA/commit/b6fc705ae5803efed59f8320b837c418f11c2ec7).

## 2026-09-15 — Documentation reconciliation and governance corpus consolidation

- Front-door surfaces (this file, `README.md`, `ROADMAP.md`, both governance indexes, the implementation crosswalk, the architecture entry point) moved from qualified ref `1aed0f4` to `4282ebc`.
- HAT — The Human-AI Team v0.3 added at [`docs/governance/HAT_THE_HUMAN_AI_TEAM_v0_3.md`](docs/governance/HAT_THE_HUMAN_AI_TEAM_v0_3.md); three Triad-era documents moved to `docs/superseded/`; four governance documents moved out of `core/migrations/governance/` to the constitution and governance folders by their own stated status; empty root `governance/` shadow files, three zero-byte named documents and six directory placeholders removed.
- Boundary: documentation and path changes only; no runtime, migration, test or lifecycle state changed. Recorded here at issue; the commit identity is recorded by the next reconciliation.

## 2026-09-15 — S015 PKT-A-3 Datacron corrected

- Commit: [`4282ebcbc6c3b942c0b6c5efa8b3ff1a01ad73e0`](https://github.com/alchemyofacceptance/INTEVIA/commit/4282ebcbc6c3b942c0b6c5efa8b3ff1a01ad73e0), superseding [`b11b2465989ae266135d7e6627e34e7481daf578`](https://github.com/alchemyofacceptance/INTEVIA/commit/b11b2465989ae266135d7e6627e34e7481daf578) of the same day.
- Record: [`D-S015-PKT-A-3-INTEVIA-LINEAGE-CHAIN-ILC.md`](docs/holocron/datacrons/D-S015-PKT-A-3-INTEVIA-LINEAGE-CHAIN-ILC.md), v0.8.
- Boundary: the correction records, from the migration bytes, that the identity split and its insert-only guardian ruled on 12 September are not delivered in migration `0022`, and that both are carried into PKT-B by Human ruling. Corrections are marked and dated; nothing rewritten.

## 2026-09-15 — S015 PKT-A-3: migration 0022, the INTEVIA Lineage-Chain (ILC)

- Implementation commit: [`5eb7783617243998e01449829b8f6c61d42b12b0`](https://github.com/alchemyofacceptance/INTEVIA/commit/5eb7783617243998e01449829b8f6c61d42b12b0)
- Datacron commit: [`b11b2465989ae266135d7e6627e34e7481daf578`](https://github.com/alchemyofacceptance/INTEVIA/commit/b11b2465989ae266135d7e6627e34e7481daf578) (v0.7; superseded above)
- Boundary: designated, implemented and landed; not accepted. Four limitations carried into PKT-B — no second-layer commitment verification (`U-14`), no Organism-as-actor (`BD-1`), resolution-table admission policy unruled (`U-6`), production database role not split (`F-U21l-04`) — plus the two undelivered rulings recorded by the correction.

## 2026-09-13 — Migration 0021 docstring corrected to the committed state

- Commit: [`845b3f0ff50e3bcfffb545d6b96caab9a2884297`](https://github.com/alchemyofacceptance/INTEVIA/commit/845b3f0ff50e3bcfffb545d6b96caab9a2884297)
- Boundary: docstring only; no SQL or model change. The committed docstring had described the file as an uncommitted candidate.

## 2026-09-12 — S015 PKT-A-2: migration 0021, the recorded chain and bitemporal fold

- Implementation commit: [`859960d8645b682f118c651d547c1327439a8b92`](https://github.com/alchemyofacceptance/INTEVIA/commit/859960d8645b682f118c651d547c1327439a8b92)
- Datacron commit: [`0f0b122574037b84e0c39e7d3599ab1fdf47f8be`](https://github.com/alchemyofacceptance/INTEVIA/commit/0f0b122574037b84e0c39e7d3599ab1fdf47f8be)
- Record: [`D-S015-PKT-A-2-GOVERNED-RECORDED-CHAIN-AND-BITEMPORAL-FOLD.md`](docs/holocron/datacrons/D-S015-PKT-A-2-GOVERNED-RECORDED-CHAIN-AND-BITEMPORAL-FOLD.md)
- Boundary: the first packet-level Datacron and the first to the fixed fourteen-section spine. Designated, implemented and landed; not accepted. PKT-A acceptance is not on the path forward, by Human ruling.

## 2026-09-01 — S015: migration 0020 model-state divergence closed

- Commit: [`3efa7f6a6b9024c8ac608619c3c8dff9276f95de`](https://github.com/alchemyofacceptance/INTEVIA/commit/3efa7f6a6b9024c8ac608619c3c8dff9276f95de)
- Boundary: closed the divergence between Django's migration state and the model classes after migration `0020`.

## 2026-08-10 — S015: migrations 0019 and 0020, the Living Organism foundation and genesis identity completeness

- Custody commit: [`93581a79e993bfdc833e5c266ff5690626b5b472`](https://github.com/alchemyofacceptance/INTEVIA/commit/93581a79e993bfdc833e5c266ff5690626b5b472), whose own subject states *THIS IS NOT ACCEPTANCE*; both migration files were subsequently touched by the 1 September commit above.
- Boundary: organism, circle and membership anchors and the genesis identity; a custody event under VCD-29, repository presence only.

## 2026-07-31 — S014 governed Education Course-definition paths added

- Implementation commit: [`4530d593127b163c1bafb8d79f03349c61d9335a`](https://github.com/alchemyofacceptance/INTEVIA/commit/4530d593127b163c1bafb8d79f03349c61d9335a)
- Datacron commit: [`1aed0f4da88209d5298e40867b08505661cfd451`](https://github.com/alchemyofacceptance/INTEVIA/commit/1aed0f4da88209d5298e40867b08505661cfd451)
- Record: [`D-S014-GOVERNED-EDUCATION-COURSE-DEFINITION.md`](docs/holocron/datacrons/D-S014-GOVERNED-EDUCATION-COURSE-DEFINITION.md)
- Boundary: repository event only; the Datacron states that direct Human acceptance and closure were pending at issue and keeps `MAT-S014-01` deferred.

## 2026-07-28 — S013 correction lineage recorded

- Corrected commit: [`c3832b48edae798f637cc7570913d466fc9e6e63`](https://github.com/alchemyofacceptance/INTEVIA/commit/c3832b48edae798f637cc7570913d466fc9e6e63)
- Record: [`D-S013-GOVERNED-SERVICE-SUBMISSION-PROFILE-EFFECT.md`](docs/holocron/datacrons/D-S013-GOVERNED-SERVICE-SUBMISSION-PROFILE-EFFECT.md)

## 2026-07-20 to 2026-07-27 — Governed implementation Slice records added

Selected repository events introduced and recorded bounded contribution, Events, CARE, Service, Identity, self-registration, Library exact-version, Event-resource, and Service-activity foundations. Use [`docs/holocron/datacrons/`](docs/holocron/datacrons/) for the exact record set and each record's declared boundary.

This grouped entry is navigation. It does not collapse the distinct commits, evidence, reviews, or Human decisions into one result.

## 2026-06-08 — Execution Policy Exception Event added to Corpus v2

The original CHANGELOG entry is preserved here as an historical repository statement. This reconciliation does not independently validate or reinterpret its deeper Corpus meaning.

## Release state

No INTEVIA software release is declared by this change record. Date headings are repository-event dates, not version releases.
