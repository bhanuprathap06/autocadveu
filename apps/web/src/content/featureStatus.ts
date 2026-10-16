/**
 * Feature status matrix shown on /status. Single source of truth: feature-status.json, which also generates
 * docs/feature-status.md (tools/gen_feature_status.py; CI fails if the two drift).
 * A capability is "implemented_and_verified" only when automated tests exercise it end to end against real engines.
 */
import data from "./feature-status.json";

export type Status = "implemented_and_verified" | "implemented_with_limitations" | "prototype" | "external_integration_required" | "planned";
export interface FeatureRow { area: string; feature: string; status: Status; evidence: string; limits?: string }

export const FEATURES = data.features as FeatureRow[];
export const STATUS_HELP = data.statusHelp as Record<Status, string>;
export const STATUS_UPDATED = data.updated;
