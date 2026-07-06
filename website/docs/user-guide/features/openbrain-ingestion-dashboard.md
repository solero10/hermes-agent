---
sidebar_position: 16
title: "OpenBrain Ingestion Dashboard"
description: "Dashboard plugin for inspecting OpenBrain ingestion snapshots, source units, thought lineage, dashboard-local archives, stops, and CortexDB receipts"
---

# OpenBrain Ingestion Dashboard

The **OpenBrain** dashboard tab is a mostly read-only view into the latest OpenBrain ingestion snapshot, with one dashboard-local archive action for hiding cards from normal review. It is meant for auditing what the ingestion exporter produced before and after import: which source units were processed, which durable thought candidates were extracted, where each candidate currently sits in the lineage, what stopped before import, which cards Ken archived from the dashboard, and which imported thoughts have CortexDB receipts.

The dashboard is registered as a dashboard plugin named `openbrain_ingestion` and appears at `/openbrain` after the Kanban tab.

## What it shows

The board is organized by **source unit**. Each row represents one item from the snapshot, such as a transcript, document, or other source-specific unit. Expanding a row now shows a dense review layout:

- a compact source summary strip;
- a per-source **Show Evidence cards (N)** control, because Evidence cards are collapsed by default per source row;
- a sticky **Candidate memory table** for the durable Thought candidates.

The Candidate memory pipeline still uses the canonical ingestion stages as table columns:

1. Evidence cards
2. Thought candidates
3. Enrich
4. Deduped
5. Ready for CortexDB
6. CortexDB

Evidence cards stay behind each source row as source-grounded nuggets. They are not shown by default and are not mixed into the Candidate memory table. Click **Show Evidence cards (N)** on a source row to display them for that source; click **Hide Evidence cards** to collapse them again. Each Evidence card shows the Evidence title and compact tags such as **used**; Thought candidates appear as stable table rows with clickable cells for Tags, **Enrich**, Deduped, Ready for CortexDB, and **CortexDB import** detail. Candidate rows can also show a subtle **via Panning** or **via Meeting** technique pill so the generation recipe or skill is visible without opening database fields.

The top metrics show **visible / total** counts for source units, thoughts, review items, stopped items, imported items, zero-thought sources, and archived cards. Visible counts follow the current filters, source-date range, search, and **Show archived thoughts** setting; total counts describe the selected source type in the snapshot.

## Source-type dropdown

Use the **Source type** dropdown to switch between material lanes in the snapshot. The backend defaults to `transcripts` when present, otherwise the first source type in the snapshot. The dropdown is populated by:

```text
/api/plugins/openbrain_ingestion/source-types
```

The board itself is loaded from:

```text
/api/plugins/openbrain_ingestion/board?source_type=<type>&filter=<filter>&sort=<sort>&search=<query>&date_from=<yyyy-mm-dd>&date_to=<yyyy-mm-dd>&include_archived=<true|false>
```

Use the **Source date from** and **Source date to** controls to filter source units by source date. The UI accepts `MM/DD/YYYY`; the API also accepts `YYYY-MM-DD`. When **Source date to** is empty, entering a complete **Source date from** automatically fills **Source date to** with the following day. For transcript sources, source date is the source occurrence/recording date when available and falls back to processed time only when no source occurrence date exists.

## Current-state rule: each card appears exactly once

The board is a current-state view, not a full per-stage history board. **Each card appears exactly once**, in the column matching its current or final stage. The first two columns use clearer Panning names: **Evidence cards** are source-grounded extracted snippets, and **Thought candidates** are shaped memory candidates that are not CortexDB memories yet. For example:

- an imported thought appears in **CortexDB**;
- a duplicate stopped during dedupe appears in **Deduped**;
- a candidate ready but not imported yet appears in **Ready for CortexDB**.

Open a card's detail dialog to see the full lineage timeline, including completed, current, pending, review-needed, and not-reached stages.

## Column-specific detail panels

The detail dialog keeps one shared shell, but the main panel changes by the card's current column. This makes the board clearer because each column represents a different kind of artifact:

- **Evidence card detail** shows source evidence: quote/snippet, source section, extraction method, coverage status, and which Thought candidate cards used it.
- **Thought candidate detail** shows Formation Trace: primary lineage cards, extra context, exact shaping input, shaped output, merge note, and gate events.
- **Enrich detail** shows recipe workflow status, enrichment recipe/method, reason, notes, and evidence notes.
- **Dedupe detail** shows dedupe evidence: semantic/exact method, duplicate/unique/merge decision, matched memory, similarity score, fingerprint, and merge note.
- **Ready detail** shows the final import package: final memory text, type/topics/people, source receipt, policy check, dedupe check, checklist, and import payload preview.
- **CortexDB detail** shows the storage receipt: thought ID, stored text, metadata, capture time, source unit ID, candidate ID, and update/merge note.

Policy decisions are still available as policy tags and stop detail when the exporter records them, but Policy is no longer a separate Candidate table column.

Older snapshots may not have every field. Missing fields are shown as empty states instead of errors.

## Formation Trace

The Thought Detail panel can show a **Formation Trace** when the snapshot includes it. This trace explains how a thought was formed from source material as it moves across the board. It may include the primary lineage cards, additional source context, the exact LLM input package, the shaped output, and later Policy/Dedupe/CortexDB gate decisions.

The trace does not show hidden model reasoning. It shows auditable inputs, outputs, and decisions so the dashboard can distinguish direct evidence from extra context that helped interpret the evidence. Candidate snapshots can include `generation_technique` at the thought, formation-trace, or producer level. The API normalizes known techniques such as `panning-for-gold` and `meeting-synthesis`, including whether the source is a recipe or a skill.

Older thoughts or snapshots without trace data show a clear “No formation trace in this snapshot” message instead of pretending the trace was empty.

## Ready for CortexDB

**Ready for CortexDB** means the candidate has survived the earlier shaping, policy, and dedupe phases and is ready to become a durable OpenBrain/CortexDB memory. In this mostly read-only dashboard, the label is informational: the dashboard does not promote, import, reopen, or mutate records outside dashboard-local archive state.

## Stopped and not-imported cards

Stopped cards are candidates or inventory rows that did not become CortexDB records. Duplicate stops remain in **Deduped**. Capture-policy stops keep a policy tag and stopped reason on the candidate row/detail rather than occupying a separate Policy column; useful historical/reference material that merely needs rewriting should remain in **Thought candidates** until recipe workflow and dedupe decide whether it stays, merges, or moves on.

Click a policy tag on a card or detail dialog to show its definition. Policy tags use this vocabulary:

- **Needs source validation** — potentially useful, but the supporting evidence is weak, outline-only, voicemail-derived, or ambiguous. Verify against the source before capture.
- **Sensitive detail** — contains a raw private identifier, case/reference/account number, emergency/contact detail, or similar information that belongs in controlled evidence rather than general memory.
- **Stale task** — looks like an old action item or status update. Do not store it as current memory until completion/current relevance is checked or rewritten as history.
- **Obsolete internal process** — old employer/company-specific process mechanics with no reusable lesson. Keep auditable in source artifacts, but do not capture as CortexDB memory.
- **Too thin / missing context** — not self-contained enough to become a reliable memory: missing who/what/why, identifiers, or enough detail to avoid misleading future retrieval.
- **No durable value** — purely incidental, time-specific, already-expired, or not useful enough to keep as long-term memory.

Their detail dialog shows:

- a stop code label;
- any stop target, matched, or related memory IDs;
- the stopped reason;
- the lineage timeline, with later stages marked **Not reached** when applicable.

If a non-imported card reaches a later stage in the snapshot, the API intentionally strips CortexDB receipt fields unless the disposition is `imported`.

## Imported receipts

Imported cards show a **CortexDB receipt** in the detail dialog, including fields such as receipt ID, type, capture time, ingestion run ID, source unit ID, and candidate ID. The receipt indicates that the candidate crossed the boundary into CortexDB/OpenBrain memory storage.

At the bottom of every detail dialog, the dashboard also lists the known `public.thoughts` database fields. Values are filled from the sanitized snapshot, CortexDB receipt, or safe database/upsert defaults when the card has actually been imported. A dash means the snapshot does not contain a value for that field.

## Archived dashboard thoughts

Use the checkbox column in the **Candidate memory table** to select one or more Thought candidates, then click **Archive selected** or **Unarchive selected** above the table. Bulk archive actions write dashboard-local state to:

```text
~/.hermes/openbrain-ingestion-dashboard/archive_state.json
```

Archiving hides dashboard cards by default and does not delete source artifacts, snapshot rows, or CortexDB memories. Turn on **Show archived thoughts** to inspect archived cards and use the table checkboxes plus **Unarchive selected** to return them to the default board. Archived cards keep their original stage and disposition plus archive metadata such as archive time, actor, and reason.

## Evidence visibility

Evidence cards are collapsed by default per source row. Click **Show Evidence cards (N)** on a source row to display the **Evidence grid** for that source. Candidate thoughts remain visible in the Candidate memory table by default.

## Snapshot path

The backend reads the sanitized snapshot from:

```text
~/.hermes/openbrain-ingestion-dashboard/snapshot.json
```

If the file is missing or unreadable, the backend returns a safe sample snapshot so the dashboard can still mount.

## Exporter boundary

The exporter boundary is:

```text
Panning-for-Gold artifacts -> SnapshotV1 -> dashboard API -> mostly read-only UI + dashboard-local archive overlay
```

The Panning exporter converts artifacts such as `source-items.jsonl`, `inventory.jsonl`, `dedupe-receipts.jsonl`, `capture-candidates.jsonl`, `capture-audit.jsonl`, and `summary.json` into a sanitized **SnapshotV1** document. The dashboard backend validates and redacts that snapshot, normalizes stage aliases, surfaces inventory rows that stop before `Ready for CortexDB` as visible stopped cards, records the candidate generation technique, preserves Enrich workflow status, strips retired recipe-stage metadata that is not part of captured-thought dashboard review, overlays dashboard-local archive state from `archive_state.json`, and exposes only the source-scoped board/detail API used by the frontend.

The frontend does not read Panning artifacts directly and does not include adapter-specific logic. It only calls the dashboard plugin API through the Hermes plugin SDK.

## Read-only MVP

This dashboard release is still deliberately **mostly read-only**. **Archive selected** and **Unarchive selected** are the only enabled dashboard mutations, and they only update `archive_state.json`. Buttons such as **Open source**, **Open CortexDB record**, **Copy final memory**, **Open matched memory**, **Copy merge note**, **Reopen later**, **Promote later**, and **Mark for review later** are disabled placeholders labeled `Read-only MVP`.

Archiving hides dashboard cards by default and does not delete source artifacts, snapshot rows, or CortexDB memories. Use the dashboard to inspect and audit ingestion state; perform source edits, merges, promotions, or imports through the ingestion/export pipeline outside this UI.
