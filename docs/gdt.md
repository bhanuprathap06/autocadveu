# GD&T support

Drawings declare a standard per sheet: **ISO GPS** (ISO 1101:2017 geometrical tolerancing, ISO 129-1 dimensioning,
ISO 128 lines/views) or **ASME Y14.5-2018**. Annotations may not mix standards. Default projection: first angle
(ISO), third angle (ASME); the projection symbol is drawn in the title block.

## Supported annotations

Feature control frames (all 14 characteristics), datum feature symbols (letters A–Z except I, O, Q), surface texture
symbols (Ra with process requirement), notes with optional leaders, toleranced dimensions (symmetric, deviation,
limits, ISO 286 fit designations), automatic overall dimensions and hole callouts measured from the model.

## Semantic rules enforced (errors block export)

1. Form tolerances (straightness, flatness, circularity, cylindricity) cannot reference datums.
2. Orientation (parallelism, perpendicularity, angularity), location (position, concentricity, symmetry) and runout
   tolerances require at least one datum.
3. ASME Y14.5-2018 removed concentricity and symmetry; they are rejected on ASME sheets (use position or profile).
4. Material condition modifiers (MMC/LMC) are not applicable to flatness, circularity, cylindricity, profile, runout,
   concentricity or symmetry; datum modifiers are not applicable to runout.
5. The diameter zone symbol applies only to characteristics that can control an axis.
6. Every referenced datum must be defined by a datum feature symbol; each letter is defined once and appears once per
   frame. Tolerance values must be positive.

## What the checks do not do

They validate the *structure* of tolerance specifications against the standards' rules. They do not judge whether a
tolerance is functionally adequate, manufacturable, or consistent with tolerance stacks — that remains engineering
judgement (the tolerance-stack calculator can help).
