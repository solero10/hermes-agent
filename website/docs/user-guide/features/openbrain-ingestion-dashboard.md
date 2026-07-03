---
sidebar_position: 16
title: "OpenBrain Ingestion Dashboard"
description: "Read-only dashboard plugin for inspecting OpenBrain ingestion snapshots, source units, thought lineage, stops, and CortexDB receipts"
---

# OpenBrain Ingestion Dashboard

The **OpenBrain** dashboard tab is a read-only view into the latest OpenBrain ingestion snapshot. It is meant for auditing what the ingestion exporter produced before and after import: which source units were processed, which durable thought candidates were extracted, where each candidate currently sits in the lineage, what stopped before import, and which imported thoughts have CortexDB receipts.

The dashboard is registered as a dashboard plugin named `openbrain_ingestion` and appears at `/openbrain` after the Kanban tab.

## What it shows

The board is organized by **source unit**. Each row represents one item from the snapshot, such as a transcript, document, or other source-specific unit. Inside each row, thought cards are grouped by canonical ingestion stage:

1. Evidence cards
2. Thought candidates
3. Policy
4. Deduped
5. Ready for CortexDB
6. CortexDB

The top metrics show **visible / total** counts for source units, thoughts, review items, stopped items, imported items, and zero-thought sources. Visible counts follow the current filters, source-date range, and search; total counts describe the selected source type in the snapshot.

## Source-type dropdown

Use the **Source type** dropdown to switch between material lanes in the snapshot. The backend defaults to `transcripts` when present, otherwise the first source type in the snapshot. The dropdown is populated by:

```text
/api/plugins/openbrain_ingestion/source-types
```

The board itself is loaded from:

```text
/api/plugins/openbrain_ingestion/board?source_type=<type>&filter=<filter>&sort=<sort>&search=<query>&date_from=<yyyy-mm-dd>&date_to=<yyyy-mm-dd>
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
- **Policy detail** shows the policy decision: pass/stop/review result, policy tag, reason, candidate text reviewed, redacted text, and fix note when available.
- **Dedupe detail** shows dedupe evidence: semantic/exact method, duplicate/unique/merge decision, matched memory, similarity score, fingerprint, and merge note.
- **Ready detail** shows the final import package: final memory text, type/topics/people, source receipt, policy check, dedupe check, checklist, and import payload preview.
- **CortexDB detail** shows the storage receipt: thought ID, stored text, metadata, capture time, source unit ID, candidate ID, and update/merge note.

Older snapshots may not have every field. Missing fields are shown as empty states instead of errors.

## Formation Trace

The Thought Detail panel can show a **Formation Trace** when the snapshot includes it. This trace explains how a thought was formed from source material as it moves across the board. It may include the primary lineage cards, additional source context, the exact LLM input package, the shaped output, and later Policy/Dedupe/CortexDB gate decisions.

The trace does not show hidden model reasoning. It shows auditable inputs, outputs, and decisions so the dashboard can distinguish direct evidence from extra context that helped interpret the evidence.

Older thoughts or snapshots without trace data show a clear “No formation trace in this snapshot” message instead of pretending the trace was empty.

## Ready for CortexDB

**Ready for CortexDB** means the candidate has survived the earlier shaping, policy, and dedupe phases and is ready to become a durable OpenBrain/CortexDB memory. In the read-only MVP, the label is informational: the dashboard does not promote, import, reopen, or mutate records.

## Stopped and not-imported cards

Stopped cards are candidates or inventory rows that did not become CortexDB records. Duplicate stops remain in **Deduped**. The **Policy** column is reserved only for thoughts blocked by a clearly defined capture policy; useful historical/reference material that merely needs rewriting should remain in **Shaped** until the policy/redaction pass runs, then dedupe can decide whether it stays, merges, or moves on.

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

## Snapshot path

The backend reads the sanitized snapshot from:

```text
~/.hermes/openbrain-ingestion-dashboard/snapshot.json
```

If the file is missing or unreadable, the backend returns a safe sample snapshot so the dashboard can still mount.

## Exporter boundary

The exporter boundary is:

```text
Panning-for-Gold artifacts -> SnapshotV1 -> dashboard API -> read-only UI
```

The Panning exporter converts artifacts such as `source-items.jsonl`, `inventory.jsonl`, `dedupe-receipts.jsonl`, `capture-candidates.jsonl`, `capture-audit.jsonl`, and `summary.json` into a sanitized **SnapshotV1** document. The dashboard backend validates and redacts that snapshot, normalizes stage aliases, surfaces inventory rows that stop before `Ready for CortexDB` as visible stopped cards, and exposes only the source-scoped board/detail API used by the frontend.

The frontend does not read Panning artifacts directly and does not include adapter-specific logic. It only calls the dashboard plugin API through the Hermes plugin SDK.

## Read-only MVP

This first dashboard release is deliberately **read-only**. Buttons such as **Open source**, **Open CortexDB record**, **Copy final memory**, **Open matched memory**, **Copy merge note**, **Reopen later**, **Promote later**, and **Mark for review later** are disabled placeholders labeled `Read-only MVP`.

No mutation controls are enabled. Use the dashboard to inspect and audit ingestion state; perform source edits, merges, promotions, or imports through the ingestion/export pipeline outside this UI.
