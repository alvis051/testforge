import { useQuery } from "@tanstack/react-query";

import { apiFetch } from "./client";
import type { FailureCategory, FlakyCase, Plan, Project, ResultRow, RunListItem, RunSummary, RunTrendPoint } from "./types";

export type RunFilters = { status?: string; source?: string };

/**
 * Hierarchical query keys. TanStack Query invalidates by key prefix, so
 * invalidating ['runs', id] catches the run and its results — which is what
 * keeps the eventual write slice from having to normalise every call site.
 */
export const keys = {
  projects: () => ["projects"] as const,
  runs: (projectKey: string, filters: RunFilters = {}) =>
    ["projects", projectKey, "runs", filters] as const,
  plans: (projectKey: string) => ["projects", projectKey, "plans"] as const,
  run: (runId: string) => ["runs", runId] as const,
  runResults: (runId: string) => ["runs", runId, "results"] as const,
  planRuns: (planId: string) => ["plans", planId, "runs"] as const,
  insightsFlaky: (projectKey: string) =>
    ["projects", projectKey, "insights", "flaky"] as const,
  insightsTrends: (projectKey: string, limit: number) =>
    ["projects", projectKey, "insights", "trends", limit] as const,
  insightsCategories: (projectKey: string, limit: number) =>
    ["projects", projectKey, "insights", "categories", limit] as const,
};

function withParams(path: string, params: Record<string, string | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value) search.set(key, value);
  }
  const query = search.toString();
  return query ? `${path}?${query}` : path;
}

export function useProjects() {
  return useQuery({
    queryKey: keys.projects(),
    queryFn: () => apiFetch<Project[]>("/api/projects"),
  });
}

export function useRuns(projectKey: string, filters: RunFilters = {}) {
  return useQuery({
    queryKey: keys.runs(projectKey, filters),
    queryFn: () =>
      apiFetch<RunListItem[]>(
        withParams(`/api/projects/${projectKey}/runs`, filters),
      ),
    enabled: Boolean(projectKey),
  });
}

export function usePlans(projectKey: string) {
  return useQuery({
    queryKey: keys.plans(projectKey),
    queryFn: () => apiFetch<Plan[]>(`/api/projects/${projectKey}/plans`),
    enabled: Boolean(projectKey),
  });
}

export function useRun(runId: string) {
  return useQuery({
    queryKey: keys.run(runId),
    queryFn: () => apiFetch<RunSummary>(`/api/runs/${runId}`),
  });
}

export function useRunResults(runId: string) {
  return useQuery({
    queryKey: keys.runResults(runId),
    queryFn: () => apiFetch<ResultRow[]>(`/api/runs/${runId}/results`),
  });
}

export function usePlanRuns(planId: string) {
  return useQuery({
    queryKey: keys.planRuns(planId),
    queryFn: () => apiFetch<RunListItem[]>(`/api/plans/${planId}/runs`),
  });
}

export function useFlakyCases(projectKey: string) {
  return useQuery({
    queryKey: keys.insightsFlaky(projectKey),
    queryFn: () =>
      apiFetch<FlakyCase[]>(`/api/projects/${projectKey}/insights/flaky`),
    enabled: Boolean(projectKey),
  });
}

export function useRunTrends(projectKey: string, limit = 20) {
  return useQuery({
    queryKey: keys.insightsTrends(projectKey, limit),
    queryFn: () =>
      apiFetch<RunTrendPoint[]>(
        `/api/projects/${projectKey}/insights/trends?limit=${limit}`,
      ),
    enabled: Boolean(projectKey),
  });
}

export function useFailureCategories(projectKey: string, limit = 20) {
  return useQuery({
    queryKey: keys.insightsCategories(projectKey, limit),
    queryFn: () =>
      apiFetch<FailureCategory[]>(
        `/api/projects/${projectKey}/insights/failure-categories?limit=${limit}`,
      ),
    enabled: Boolean(projectKey),
  });
}
