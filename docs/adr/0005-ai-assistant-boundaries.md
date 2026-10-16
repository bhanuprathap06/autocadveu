# ADR-0005: AI assistant boundaries — structured outputs, claim classification, validated proposals, opt-in egress

- Status: Accepted
- Date: 2026-10-16

## Decision

- The assistant only produces **schema-validated structured outputs** (requirements, concepts, parametric proposals).
  It never emits geometry scripts, SQL, shell or solver commands. Proposals can only select a registered parametric
  template, set its named parameters and pick a material from the library; the server builds the document with the
  same builder used everywhere else and **regenerates it in the CAD kernel** before showing it. Nothing is saved until
  a user approves it.
- Every claim is labelled: source-supported fact, calculation, engineering hypothesis, design recommendation or
  unverified suggestion. Extracted requirements are *proposed* and must be approved or rejected individually.
- The default provider is a deterministic local rules engine. Claude is used only when an administrator configures it
  **and** the user ticks "use external AI" for that request. CAD files are never sent; only the problem statement and
  requirements text. Model refusals, truncation and rate limits are surfaced as errors, not papered over.
- Verification of requirements uses computed evidence only (CAD mass/size from the kernel, simulation results).

## Consequences

AI assistance is useful for structuring work but cannot introduce unvalidated geometry or uncited facts into a model.
