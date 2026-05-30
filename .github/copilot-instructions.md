# QCS Oracle Automation — Hard Architectural Invariants

## Two-Phase Architecture
AI is used **only** at record time and during failure healing.
Normal pytest replay **must** be AI-free and fully deterministic.

## Recorder Migration: Approach A → Approach B
We are actively migrating the recorder from Approach A to Approach B.

**Approach A (legacy, coordinate-based — healing fallback only):**
screenshot → computer-use AI returns pixel coordinates → `coord_to_element` mapping → `java.awt.Robot` coordinate click

**Approach B (target):**
scan Java DOM → render AI-friendly action snapshot → AI returns `element_id + action + value` → resolve `element_id` to a repo element → execute deterministically via Java-agent locator params

`qcs_replay/healing/coord_to_locator.py` and `java.awt.Robot` coordinate clicks are retained **only** as a healing fallback.
Do not use them in new recorder or replay code.

## Object Repository Primary Key
Keyed by `(form_ref, element_ref)` in `qcs_repo/schema.py` (`RepoEntry`).
**Never change this primary key.** Do not introduce `target_ref` or any alternative keying scheme.

## Deterministic Execution Path
All replay actions **must** go through `locator_params` in `qcs_java_agent/snapshot.py` and the Java-agent `ActionExecutor`.
Do **not** use `java.awt.Robot` coordinate clicks outside the healing fallback.

## Oracle BLOCK.ITEM Names
Do **not** fabricate Oracle `BLOCK.ITEM` names.
Gold-standard names come from an offline EBS metadata dictionary joined at record time.
They ride along as attributes on repo entries — **never** as the primary key.