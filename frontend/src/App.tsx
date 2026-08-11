import { Link, Navigate, Outlet, Route, Routes, useNavigate, useParams } from "react-router-dom";

import { useProjects } from "./api/queries";
import { ErrorState } from "./components/ErrorState";
import { InsightsPage } from "./pages/InsightsPage";
import { PlansPage } from "./pages/PlansPage";
import { RunDetailPage } from "./pages/RunDetailPage";
import { RunsPage } from "./pages/RunsPage";

function Shell() {
  const { data: projects } = useProjects();
  const { projectKey } = useParams<{ projectKey: string }>();
  const navigate = useNavigate();
  const active = projectKey ?? projects?.[0]?.key ?? "";

  return (
    <>
      <header className="shell-header">
        <h1>TestForge</h1>
        {active && (
          <nav>
            <Link to={`/projects/${active}/runs`}>Runs</Link>
            <Link to={`/projects/${active}/plans`}>Plans</Link>
            <Link to={`/projects/${active}/insights`}>Insights</Link>
          </nav>
        )}
        {projects && projects.length > 0 && (
          <select
            data-testid="project-picker"
            value={active}
            onChange={(e) => navigate(`/projects/${e.target.value}/runs`)}
          >
            {projects.map((project) => (
              <option key={project.key} value={project.key}>
                {project.key} — {project.name}
              </option>
            ))}
          </select>
        )}
      </header>
      <main className="shell-main">
        <Outlet />
      </main>
    </>
  );
}

function Landing() {
  const { data: projects, isLoading, error } = useProjects();
  if (isLoading) return <p className="muted">Loading…</p>;
  if (error) return <ErrorState error={error} />;
  if (!projects?.length) return <p className="muted">No projects yet.</p>;
  return <Navigate to={`/projects/${projects[0].key}/runs`} replace />;
}

export function App() {
  return (
    <Routes>
      <Route element={<Shell />}>
        <Route path="/" element={<Landing />} />
        <Route path="/projects/:projectKey/runs" element={<RunsPage />} />
        <Route path="/projects/:projectKey/plans" element={<PlansPage />} />
        <Route path="/projects/:projectKey/insights" element={<InsightsPage />} />
        <Route path="/runs/:runId" element={<RunDetailPage />} />
      </Route>
    </Routes>
  );
}
