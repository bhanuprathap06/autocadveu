# Backlog (phased)

Status of everything already built is in [feature-status.md](feature-status.md). This backlog lists the next work,
ordered by phase. Items marked ⚑ address a limitation stated in the feature-status page.

## Next (hardening)

- ⚑ Nonce-based CSP to drop `'unsafe-inline'` from `script-src`.
- ⚑ SSO (OIDC/SAML) and SCIM provisioning for organisations.
- ⚑ ClamAV (or ICAP) malware-scanning integration for uploads; quarantine until clean.
- Per-organisation storage quotas and usage reporting.
- Load test API + kernel pool + workers (target: 200 concurrent editors per API node) and publish capacity curves.
- Visual regression tests for drawings (PDF/SVG raster diff) and the 3D viewport.

## Modelling

- ⚑ Sketch trim/extend/offset/fillet tools; ellipse and slot tools in the editor; dimension-driven sketch patterns.
- ⚑ Sweep with guide curves and twist; loft with tangency conditions; draft and rib features; variable-radius fillets.
- Configurations (design tables) on top of parameters.
- ⚑ Feature-level merge for concurrent edits (today conflicts are resolved explicitly per document).
- Offline read-only viewer using the WASM kernel for review without a server round trip.

## Assemblies and motion

- Gear/rack/cam mates; mate groups; sub-assembly flexible states.
- Rigid-body dynamics (masses, forces, contact) for mechanism studies — only after validation against analytical cases.

## Simulation

- ⚑ Modal and linear buckling (CalculiX `*FREQUENCY`, `*BUCKLE`) with validation cases.
- ⚑ Transient thermal and sequential thermo-mechanical coupling.
- ⚑ Contact and bolt preload; nonlinear materials.
- ⚑ CFD adapter (OpenFOAM: snappyHexMesh + simpleFoam) as an external integration with its own validation suite.
- Fatigue post-processing (S-N based) with explicit assumptions.

## Data and integrations

- Manufacturer catalog import pipeline (CSV/JSON with mandatory provenance fields, licence review queue).
- PLM/ERP export of BOMs; webhook notifications.
- Additional import formats via licensed converters (Parasolid, JT) as optional integrations.
