import type { components } from "./schema";

/** Named aliases over the generated schema, so pages never index into `components`. */
export type Project = components["schemas"]["ProjectOut"];
export type RunListItem = components["schemas"]["RunListItemOut"];
export type RunSummary = components["schemas"]["RunSummaryOut"];
export type ResultRow = components["schemas"]["ResultOut"];
export type Plan = components["schemas"]["PlanOut"];
export type PlanCase = components["schemas"]["PlanCaseOut"];
export type PlanProgress = components["schemas"]["PlanProgress"];
export type FlakyCase = components["schemas"]["FlakyCaseOut"];
export type RunTrendPoint = components["schemas"]["RunTrendPointOut"];
export type FailureCategory = components["schemas"]["FailureCategoryOut"];
