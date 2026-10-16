import type { components } from "./schema";

type S = components["schemas"];

// Persistent document models (generated from the server's Pydantic schemas).
export type PartDocument = S["Document"];
export type Feature = PartDocument["features"][number];
export type FeatureType = Feature["type"];
export type SketchFeature = S["SketchF"];
export type SketchParams = S["SketchParams"];
export type SketchEntity = SketchParams["entities"][number];
export type SkConstraint = S["SkConstraint"];
export type PlaneRef = S["PlaneRef"];
export type AxisRef = S["AxisRef"];
export type EdgeRef = S["EdgeRef"];
export type Parameter = S["Parameter"];
export type MaterialAssignment = S["MaterialAssignment"];
export type AssemblyDocument = S["AssemblyDocument"];
export type AssemblyInstance = S["Instance"];
export type Mate = S["Mate"];
export type MateRef = S["MateRef"];
export type DrawingContent = S["DrawingContent"];
export type StaticSetup = S["StaticSetup"];
export type ThermalSetup = S["ThermalSetup"];

// Response shapes (mirroring server outputs documented in the API reference).
export interface User {
  id: string;
  email: string;
  display_name: string;
  email_verified: boolean;
  mfa_enabled: boolean;
  is_admin: boolean;
  preferences: Record<string, unknown>;
}
export interface Org { id: string; name: string; personal: boolean; role: string }
export interface Me { user: User; organizations: Org[] }

export type Role = "owner" | "editor" | "reviewer" | "viewer";
export interface Project {
  id: string;
  org_id: string;
  name: string;
  description: string;
  domain: string | null;
  template: string | null;
  created_at: string;
  updated_at: string;
  role: Role | null;
  document_count?: number;
}
export interface DocMeta {
  id: string;
  project_id?: string;
  name: string;
  kind: "part" | "assembly";
  head_revision: number;
  updated_at: string;
  role?: Role | null;
}
export interface FeatureStatus {
  id: string;
  type: string;
  status: "ok" | "warning" | "error" | "suppressed" | "skipped";
  message: string;
  code: string;
  warnings: string[];
  cached?: boolean;
  elapsed_ms?: number;
  info?: unknown;
}
export interface Revision {
  number: number;
  parent_number: number | null;
  restored_from: number | null;
  message: string;
  content_sha256: string;
  kernel_version: string;
  created_by: string;
  author?: string | null;
  created_at: string;
  ok: boolean | null;
  recompute: { ok?: boolean; features?: FeatureStatus[]; bodies?: { name: string; mass?: MassProps }[]; solve?: AsmSolve } | null;
  content?: PartDocument | AssemblyDocument;
}
export interface MassProps {
  volume_mm3: number;
  surface_area_mm2: number;
  centroid_mm: number[];
  bbox_mm: [number[], number[]] | null;
  solids: number;
  valid: boolean;
  mass_kg?: number;
  density_kg_m3?: number;
  inertia_kg_mm2?: number[][];
  principal_moments_kg_mm2?: number[];
  error?: string;
}
export interface FaceMeta {
  name: string;
  names: string[];
  start: number;
  count: number;
  stable: boolean;
  geometry?: Record<string, unknown> & { surface: string; area: number; centroid: number[]; normal?: number[]; radius?: number };
}
export interface EdgeMeta {
  ref: { kind: "between"; faces: string[]; hint: number[] };
  points: string;
  n: number;
  curve: string;
  length: number;
  radius: number | null;
  geometry?: Record<string, number[]>;
}
export interface BodyMesh {
  name: string;
  positions: string;
  normals: string;
  indices: string;
  vertex_count: number;
  triangle_count: number;
  faces: FaceMeta[];
  edges: EdgeMeta[];
  vertices: number[][];
  bbox: [number[], number[]] | null;
  mass: MassProps;
  material: MaterialAssignment | null;
}
export interface SketchSolve {
  status: "empty" | "fully_constrained" | "under_constrained" | "over_constrained" | "conflicting";
  dof: number;
  converged: boolean;
  residual: number;
  iterations: number;
  free_entities: string[];
  redundant: string[];
  conflicting: string[];
  unsatisfied: string[];
  constraint_errors: Record<string, string>;
  elapsed_ms: number;
}
export interface Region {
  id: string;
  depth: number;
  area: number;
  polygon: number[][];
  hole_polygons: number[][][];
}
export interface ProfileInfo {
  regions: Region[];
  default_regions: string[];
  errors: { code: string; message: string; entities?: string[]; at?: number[] }[];
  warnings: { code: string; message: string; entities?: string[] }[];
}
export interface SketchState {
  plane: { origin: number[]; normal: number[]; x_dir: number[]; y_dir: number[] };
  solve: SketchSolve;
  profile: ProfileInfo;
  points: Record<string, number[]>;
  radii: Record<string, number>;
}
export interface PartModel {
  ok: boolean;
  kernel: string;
  elapsed_ms: number;
  parameters: Record<string, number>;
  parameter_errors: Record<string, string>;
  features: FeatureStatus[];
  sketches: Record<string, SketchState>;
  datums: Record<string, { kind: string; origin: number[]; normal?: number[]; direction?: number[] }>;
  bodies: BodyMesh[];
  cache?: "hit" | "miss";
  timing_ms?: { recompute: number; tessellate: number };
  content_sha256?: string;
}
export interface AsmSolve {
  status: string;
  converged: boolean;
  dof: number;
  residual: number;
  instance_dof: Record<string, number>;
  redundant: string[];
  conflicting: string[];
  errors: Record<string, string>;
  joint_values: Record<string, number>;
  limit_violations: { mate: string; value: number; limits: number[] }[];
}
export interface AsmInstanceOut {
  id: string;
  name: string;
  group: string | null;
  part_key: string | null;
  provenance: Record<string, unknown> | null;
  matrix: number[][];
  placement: { position: number[]; rotation: number[] };
  fixed: boolean;
  suppressed: boolean;
  color: string | null;
  dof: number | null;
  error: string | null;
}
export interface BomRow {
  item: number;
  name: string;
  quantity: number;
  part_number?: string | null;
  manufacturer?: string | null;
  material: string | null;
  unit_mass_kg: number | null;
  total_mass_kg: number | null;
  mass_basis: string | null;
  source: Record<string, unknown>;
  instances?: string[];
}
export interface AssemblyModel {
  kind: "assembly";
  ok: boolean;
  parts: Record<string, { bodies: BodyMesh[]; ok: boolean; features: FeatureStatus[] }>;
  instances: AsmInstanceOut[];
  groups: { id: string; name: string; parent: string | null }[];
  solve: AsmSolve;
  bom: BomRow[];
}
export interface CatalogItem {
  id: string;
  name: string;
  category: string;
  subcategory: string | null;
  domain: string;
  asset_class: "verified_public" | "manufacturer" | "user_upload" | "generated";
  manufacturer: string | null;
  part_number: string | null;
  standard: string | null;
  material: string | null;
  mass_kg: number | null;
  verification_status: string;
  completeness: number;
  safety_critical: boolean;
  tags: string[];
  has_cad: boolean;
  image_attribution: string | null;
  source_url: string | null;
  source_retrieved_at: string | null;
  license: string | null;
  description?: string;
  specs?: Record<string, { value: unknown; unit: string | null; basis: string; source: string | null; verified: boolean; note: string }>;
  spec_source?: string | null;
  compatibility_notes?: string;
  measurement_basis?: string | null;
  license_detail?: { name: string; spdx_id: string | null; url: string | null; redistribution_allowed: boolean; attribution_required: boolean; commercial_use_allowed: boolean; notes: string } | null;
  distance?: number;
  compared_on?: string[];
}
export interface SimSummary {
  id: string;
  name: string;
  analysis: "static" | "thermal";
  body: string;
  document_id: string;
  revision: number;
  solver: string;
  solver_version: string | null;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled" | "timed_out";
  progress: { stage: string; fraction: number } | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  cancel_requested: boolean;
  setup?: Record<string, any>;
  result?: {
    summary: Record<string, any>;
    mesh: Record<string, any>;
    convergence: Record<string, any> | null;
    warnings: string[];
    artifacts: Record<string, { sha256: string; size: number; content_type: string }>;
  };
  run?: Record<string, any> | null;
}
export interface Requirement {
  id: string;
  problem_id: string | null;
  kind: "functional" | "constraint" | "target_metric" | "assumption" | "open_question";
  text: string;
  metric: string | null;
  comparator: string | null;
  target_value: number | null;
  unit: string | null;
  source: "user" | "ai" | "heuristic";
  claim_class: string | null;
  status: "proposed" | "approved" | "rejected";
  verification: Record<string, any> | null;
}
export interface Problem {
  id: string;
  project_id: string;
  title: string;
  statement: string;
  domain: string | null;
  status: "draft" | "extracted" | "approved";
  extraction: Record<string, any> | null;
  extraction_provider: string | null;
  requirements: Requirement[];
}
export interface Comment {
  id: string;
  target_type: string;
  target_id: string;
  anchor: Record<string, any> | null;
  body: string;
  author: { id: string; display_name: string };
  parent_id: string | null;
  resolved: boolean;
  created_at: string;
}
export interface Template { key: string; name: string; domain: string; category: string; description: string; caveat: string; status: string }
