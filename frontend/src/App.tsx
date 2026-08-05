import { Link, Navigate, Route, Routes, useNavigate, useParams } from "react-router-dom";

import { useProjects } from "./api/queries";
import { ErrorState } from "./components/ErrorState";
import { PlansPage } from "./pages/PlansPage";
import { RunDetailPage } from "./pages/RunDetailPage";
import { RunsPage } from "./pages/RunsPage";

function Shell({ children }: { children: React.ReactNode }) {
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
      <main className="shell-main">{children}</main>
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
    <Shell>
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route path="/projects/:projectKey/runs" element={<RunsPage />} />
        <Route path="/projects/:projectKey/plans" element={<PlansPage />} />
        <Route path="/runs/:runId" element={<RunDetailPage />} />
      </Routes>
    </Shell>
  );
}
