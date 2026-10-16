import type { AxisRef, EdgeRef, Feature, FeatureType, PartDocument, PlaneRef } from "@/api/types";

/** Fully-populated reference objects (the generated types mirror the server's serialised documents). */
export const planeRef = (p: Partial<PlaneRef> = {}): PlaneRef => ({
  kind: "standard", name: "XY", body: null, face: null, datum: null, offset: 0, flip: false, ...p,
});

export const axisRef = (a: Partial<AxisRef> = {}): AxisRef => ({
  kind: "standard", name: "Z", datum: null, body: null, face: null, edge: null, sketch: null, entity: null, origin: null,
  direction: null, reverse: false, ...a,
});

export const edgeRef = (e: Partial<EdgeRef> = {}): EdgeRef => ({ kind: "between", faces: null, face: null, hint: null, ...e });

export const FEATURE_LABEL: Record<FeatureType, string> = {
  sketch: "Sketch", extrude: "Extrude", revolve: "Revolve", sweep: "Sweep", loft: "Loft", hole: "Hole", fillet: "Fillet",
  chamfer: "Chamfer", shell: "Shell", boolean: "Boolean", linear_pattern: "Linear pattern", circular_pattern: "Circular pattern",
  mirror: "Mirror", datum_plane: "Datum plane", datum_axis: "Datum axis", primitive: "Primitive", transform: "Move / copy body",
  import: "Imported solid",
};

export function lastSketch(doc: PartDocument, before?: string): string {
  let id = "";
  for (const f of doc.features) {
    if (before && f.id === before) break;
    if (f.type === "sketch" && !f.suppressed) id = f.id;
  }
  return id;
}

/** New feature with server-default parameters; references are pre-filled from context where possible. */
export function newFeature(type: FeatureType, id: string, ctx: { doc: PartDocument; body: string | null; name: string }): Feature {
  const base = { id, name: ctx.name, suppressed: false };
  const body = ctx.body ?? "";
  const solid = { operation: ctx.body ? "join" as const : "new" as const, body: ctx.body ?? null };
  const sk = lastSketch(ctx.doc);
  switch (type) {
    case "sketch":
      return { ...base, type, params: { plane: planeRef(), entities: [], constraints: [] } };
    case "extrude":
      return { ...base, type, params: { ...solid, sketch: sk, regions: null, distance: 10, direction: "normal", extent: "blind" } };
    case "revolve":
      return { ...base, type, params: { ...solid, sketch: sk, regions: null, axis: axisRef({ name: "Y" }), angle: 360 } };
    case "sweep":
      return { ...base, type, params: { ...solid, sketch: sk, regions: null, path_sketch: "", path_entities: null } };
    case "loft":
      return { ...base, type, params: { ...solid, sketches: [], ruled: false } };
    case "hole":
      return { ...base, type, params: { body, plane: planeRef(), positions: [[0, 0]], diameter: 6.6, end: "through_all", depth: null,
        cbore_diameter: null, cbore_depth: null, csink_diameter: null, csink_angle: 90 } };
    case "fillet":
      return { ...base, type, params: { body, edges: [], radius: 1 } };
    case "chamfer":
      return { ...base, type, params: { body, edges: [], distance: 1 } };
    case "shell":
      return { ...base, type, params: { body, remove_faces: [], thickness: 2 } };
    case "boolean":
      return { ...base, type, params: { operation: "join", target: body, tools: [], keep_tools: false } };
    case "linear_pattern":
      return { ...base, type, params: { body, features: null, direction: axisRef({ name: "X" }), count: 2, spacing: 10, direction2: null,
        count2: 1, spacing2: 10 } };
    case "circular_pattern":
      return { ...base, type, params: { body, features: null, axis: axisRef({ name: "Z" }), count: 4, angle: 360 } };
    case "mirror":
      return { ...base, type, params: { body, features: null, plane: planeRef({ name: "YZ" }) } };
    case "datum_plane":
      return { ...base, type, params: { plane: planeRef({ offset: 10 }) } };
    case "datum_axis":
      return { ...base, type, params: { axis: axisRef() } };
    case "primitive":
      return { ...base, type, params: { operation: "new", body: null, shape: "box", size: [20, 20, 20], origin: [0, 0, 0], axis: [0, 0, 1] } };
    case "transform":
      return { ...base, type, params: { body, translate: [0, 0, 0], rotate_axis: null, rotate_angle: 0, copy_to: null } };
    case "import":
      return { ...base, type, params: { blob_key: "", sha256: "0".repeat(64), filename: "", format: "step", body: null } };
  }
}

/** Ids of features whose parameters mention ``id`` (dependency heuristic for delete warnings). */
export function dependentsOf(doc: PartDocument, id: string): string[] {
  const needle = `"${id}"`;
  const prefix = `"${id}:`;
  return doc.features.filter((f) => f.id !== id && (JSON.stringify(f.params).includes(needle) || JSON.stringify(f.params).includes(prefix))).map((f) => f.id);
}
