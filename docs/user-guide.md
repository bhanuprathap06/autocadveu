# User guide

## Getting started

1. Create an account and verify your email (required before inviting or accepting invitations in production).
2. **New project** → choose a starter template (bracket, brake rotor, stepped shaft, flange coupling, battery
   enclosure, sensor housing, control-arm plate, cooling plate) or start empty. Templates are parametric
   illustrations, not validated production parts; each shows its caveat.
3. Optional: enable two-factor authentication under **Settings → Password & MFA**.

## Part workbench

- **Feature palette** (left): sketch, extrude, revolve, sweep, loft, hole, fillet, chamfer, shell, boolean, linear and
  circular pattern, mirror, datum plane/axis, primitive, move/copy. New features open a property panel on the right;
  the 3D view previews the change before you press **Create**.
- **History** tree: double-click to edit, right-click for rename, suppress, roll back to here, reorder, duplicate,
  delete (with a warning listing dependent features). Status icons show errors and warnings; the console lists
  messages with error codes.
- **Parameters**: named values and expressions (`width = 120`, `hole_x = width/2 - 10`, `2 in`). Dimensions anywhere
  can reference parameters. Bare numbers are millimetres/degrees; the units selector changes how new values are entered.
- **Selection**: click faces (Shift/Ctrl to add), switch the filter to Edge or Body. The inspector shows the face's
  persistent name, surface type, area and normal; **Measure** gives exact distances, angles, areas, lengths and radii
  from the B-rep (not the display mesh). Faces marked "unstable name" may not survive edits if referenced.
- **View**: display modes, orthographic projection, section plane, standard views (keys 1/3/7/0), fit (F), view
  bookmarks, show sketches. Command palette: Ctrl+K. Shortcut list: `?`.
- **Bodies** tab: mass properties (volume, area, mass, centroid, bounding box, principal moments) and material
  assignment from the sourced material library.
- **Comments**: pin review comments to a face and camera view; resolve them when addressed.

### Sketch editor

Double-click a sketch (or create one; a selected planar face becomes the sketch plane). Tools: line (chain; click the
first point to close), rectangle, circle, arc (centre, start, end counter-clockwise), point, spline; construction
geometry (X). Constraints apply to the selection: coincident, horizontal (H), vertical (V), parallel, perpendicular,
tangent, concentric, equal, symmetric, midpoint, fix. **Dimension** (D) picks length, distance, angle, radius or
diameter from the selection; type a number or expression. The status bar shows solver status (fully constrained,
under-constrained with remaining DOF, redundant, conflicting), the number of closed regions and profile problems
(open chains, self-intersections). A constraint that would conflict or is redundant is refused with an explanation.

### Saving, history and conflicts

Changes autosave a few seconds after you stop editing; **Version** saves a named version. Every save is an immutable
revision. **History** lists revisions with author, message, model status and content hash; compare any revision with
the head, open it read-only, or restore it (restore creates a new revision). If someone else saved in the meantime,
you choose: load theirs, save yours as a new document, or save yours on top. Unsaved changes are also kept in your
browser and offered for recovery if the tab closes.

### Export

STEP, IGES, BREP, STL, OBJ, glTF and the native format. Exact formats are re-imported and volume-checked before the
download starts; the notification shows the checksum and verification result.

## Assemblies

Insert parts from the project (following the head revision or pinned to a revision) or components with geometry from
the library. The first component is fixed. Select faces/edges on two components and choose a mate (fixed,
coincident, concentric, parallel, perpendicular, distance, angle, revolute, slider); the toolbar shows remaining
degrees of freedom, and redundant or conflicting mates are named. **Interference** checks exact overlap volumes and
clearances. **Motion** drives a revolute or slider joint through a range, plays it back, plots speed and
acceleration of a component and checks collisions at every step (quasi-static: no masses or forces). **BOM** lists
quantities, materials and masses with their basis; export CSV.

## Simulation

**Project → Simulations → New simulation**: choose the part and body (the run is tied to that revision), the analysis
(linear static or steady-state thermal), material (from the body, the library, or entered with a source), boundary
conditions by clicking faces (fixed supports, forces, pressure, gravity; temperatures, heat flux, convection), mesh
size and an optional refinement check. Results show contours (von Mises, displacement with exaggeration,
temperature), probe values, mesh statistics, equilibrium check, warnings and the solver input/output files.

## Drawings

Create a drawing from a part or assembly (pin it to a revision for release). Views, overall dimensions and hole
callouts are generated from the model; set sheet size, scale, standard (ISO/ASME), projection, title block and
revision table; add feature control frames, datums, surface finish and notes by clicking near a view. GD&T rule
violations are listed and block export until fixed. Export PDF, DXF or SVG. A drawing pinned to an older revision is
marked when the model has changed.

## Requirements and AI assistant

Write a problem statement; **Extract requirements** proposes functional requirements, constraints and measurable
targets, each with a claim classification. Approve or reject each, then approve the problem. Generate concepts (with
catalog matches) or a parametric proposal for a template; the proposal is rebuilt in the CAD kernel and only becomes
a part when you approve it. Verify numeric requirements against CAD mass/size or a finished simulation. External AI is
used only if your administrator configured it and you tick "use external AI" for that request; CAD files are never sent.

## Component library and comparison

Search by text, category, domain, verification status, 3D availability, completeness and numeric spec ranges
(`bore_d:20..30`). Each item shows the source and basis of every value, the licence and a completeness score.
Compare up to eight items side by side with synced 3D views; values are unit-normalised, missing values stay missing,
and pairwise assessments never declare parts interchangeable.

## Collaboration rooms

Create a private room, share projects you own into it, and invite people by email with a role (viewer, reviewer,
editor). Invitations are single-use, expire, and only work for the invited address. Owners can change roles, revoke
invitations, remove members and read the room's audit log.

## Engineering caveats

- Results support, but do not replace, qualified engineering judgement, physical testing and certification.
- Linear static results ignore contact, plasticity, large deformation, fatigue, buckling, residual stress and
  tolerances. Peak stresses at sharp corners and point-like supports are mesh-dependent singularities.
- Material values in the library are typical or nominal reference values with stated sources; use certified supplier
  data for design.
- Standard-derived catalog geometry represents boundary dimensions only; manufacturer ratings and details differ.
- Safety-critical components (brakes, steering, suspension, battery cells) require independent verification and,
  where applicable, homologation before any substitution.
