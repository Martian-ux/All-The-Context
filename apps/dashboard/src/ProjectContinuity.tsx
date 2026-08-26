import { BookOpenText, RefreshCw, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { ProjectCapsule, ProjectCapsuleItem, ProjectCapsuleSection, ProjectSummary } from "./types";

const SECTION_LABELS: ReadonlyArray<{ key: ProjectCapsuleSection; label: string }> = [
  { key: "current_goal", label: "Current goal" },
  { key: "decisions", label: "Decisions" },
  { key: "constraints_preferences", label: "Constraints & preferences" },
  { key: "blockers", label: "Blockers" },
  { key: "recent_meaningful_changes", label: "Recent meaningful changes" },
];

function continuityErrorMessage(error: unknown): string {
  if (error instanceof Error && error.message === "Core is not reachable on this device.") return error.message;
  return "Project Continuity is unavailable right now.";
}

function itemCountLabel(count: number): string {
  return `${count} ${count === 1 ? "item" : "items"}`;
}

function projectName(project: ProjectSummary): string {
  return project.name ?? "Unnamed project";
}

function capsuleItemCount(capsule: ProjectCapsule): number {
  return SECTION_LABELS.reduce((count, section) => count + capsule.sections[section.key].length, 0);
}

function CapsuleSection({ label, items }: { label: string; items: ProjectCapsuleItem[] }) {
  if (items.length === 0) return null;
  return (
    <section className="project-capsule-section" aria-labelledby={`project-capsule-${label.toLowerCase().replaceAll(/[^a-z0-9]+/g, "-")}`}>
      <div className="section-heading compact">
        <h3 id={`project-capsule-${label.toLowerCase().replaceAll(/[^a-z0-9]+/g, "-")}`}>{label}</h3>
        <span>{itemCountLabel(items.length)}</span>
      </div>
      <ul className="project-capsule-items">
        {items.map((item) => <li key={item.evidence_id}>{item.text}</li>)}
      </ul>
    </section>
  );
}

function ProjectContinuity() {
  const [projects, setProjects] = useState<ProjectSummary[] | null>(null);
  const [projectsLoading, setProjectsLoading] = useState(true);
  const [projectsError, setProjectsError] = useState<string | null>(null);
  const [assignmentCounts, setAssignmentCounts] = useState<{ unresolved: number; ambiguous: number } | null>(null);
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [capsule, setCapsule] = useState<ProjectCapsule | null>(null);
  const [capsuleLoading, setCapsuleLoading] = useState(false);
  const [capsuleError, setCapsuleError] = useState<string | null>(null);
  const capsuleSequence = useRef(0);

  const loadProjects = useCallback(async () => {
    setProjectsLoading(true);
    try {
      const result = await api.projects();
      setProjects(result.items);
      setAssignmentCounts({ unresolved: result.unresolved_count, ambiguous: result.ambiguous_count });
      setProjectsError(null);
      setSelectedProjectId((current) => current && result.items.some((project) => project.project_id === current) ? current : null);
      setCapsule((current) => current && result.items.some((project) => project.project_id === current.project_id) ? current : null);
    } catch (error) {
      capsuleSequence.current += 1;
      setProjects(null);
      setAssignmentCounts(null);
      setSelectedProjectId(null);
      setCapsule(null);
      setCapsuleLoading(false);
      setCapsuleError(null);
      setProjectsError(continuityErrorMessage(error));
    } finally {
      setProjectsLoading(false);
    }
  }, []);

  const loadCapsule = useCallback(async (projectId: string) => {
    const sequence = ++capsuleSequence.current;
    setCapsuleLoading(true);
    setCapsuleError(null);
    setCapsule(null);
    try {
      const result = await api.projectCapsule(projectId);
      if (sequence !== capsuleSequence.current) return;
      setCapsule(result);
    } catch (error) {
      if (sequence === capsuleSequence.current) setCapsuleError(continuityErrorMessage(error));
    } finally {
      if (sequence === capsuleSequence.current) setCapsuleLoading(false);
    }
  }, []);

  useEffect(() => { void loadProjects(); }, [loadProjects]);

  function selectProject(projectId: string) {
    setSelectedProjectId(projectId);
    void loadCapsule(projectId);
  }

  const selectedProject = projects?.find((project) => project.project_id === selectedProjectId) ?? null;
  const notAssignedCount = assignmentCounts ? assignmentCounts.unresolved + assignmentCounts.ambiguous : null;

  return (
    <section className="project-continuity" aria-labelledby="project-continuity-heading" aria-busy={projectsLoading || capsuleLoading}>
      <div className="project-continuity-heading">
        <div>
          <span className="eyebrow">Project Continuity</span>
          <h2 id="project-continuity-heading">Keep the active project in view</h2>
          <p>Read a bounded, derived briefing from Core without changing current memory or truth accounting.</p>
        </div>
        <div className="project-continuity-status">
          <ShieldCheck size={16} />
          <span>Derived · read-only</span>
        </div>
      </div>

      {projectsLoading && projects === null ? <p className="project-continuity-status-line" role="status">Loading projects…</p> : null}
      {projectsError ? (
        <div className="project-continuity-error" role="status">
          <span>{projectsError}</span>
          <button className="notice-action" type="button" onClick={() => void loadProjects()}><RefreshCw size={12} /> Retry projects</button>
        </div>
      ) : null}

      {projects ? (
        <div className="project-continuity-assignment" aria-label="Project assignment coverage">
          <strong>Not assigned</strong>
          <span>{notAssignedCount === null ? "—" : notAssignedCount}</span>
          <small>Unresolved and ambiguous records are excluded from project capsules.</small>
        </div>
      ) : null}

      {projects && projects.length === 0 ? (
        <div className="project-continuity-empty">
          <BookOpenText size={21} />
          <strong>No projects available</strong>
          <p>Core has not returned any resolved projects yet. Records that are unresolved or ambiguous are not presented as projects.</p>
        </div>
      ) : null}

      {projects && projects.length > 0 ? (
        <>
          <div className="project-continuity-workspace">
            <div className="project-list" aria-label="Projects">
              <div className="project-list-heading"><span>Projects</span><span>{projects.length}</span></div>
              {projects.map((project) => (
                <button
                  className={`project-list-row ${selectedProjectId === project.project_id ? "project-list-row--selected" : ""}`}
                  key={project.project_id}
                  type="button"
                  aria-pressed={selectedProjectId === project.project_id}
                  onClick={() => selectProject(project.project_id)}
                >
                  <span className="project-list-row-copy">
                    <strong>{projectName(project)}</strong>
                    <small>{itemCountLabel(project.item_count)} in Core</small>
                  </span>
                  <span className="project-list-row-action" aria-hidden="true">View</span>
                </button>
              ))}
            </div>

            <div className="project-capsule" aria-live="polite">
              {!selectedProject ? (
                <div className="project-capsule-empty"><BookOpenText size={20} /><p>Select a project to load its current continuity capsule.</p></div>
              ) : (
                <>
                  <div className="project-capsule-heading">
                    <div>
                      <span className="eyebrow">Selected project</span>
                      <h3>{projectName(selectedProject)}</h3>
                    </div>
                    <span className="state-token state-token--neutral">Read-only</span>
                  </div>
                  {capsuleLoading ? <p className="project-continuity-status-line" role="status">Loading continuity capsule…</p> : null}
                  {capsuleError ? (
                    <div className="project-continuity-error" role="status">
                      <span>{capsuleError}</span>
                      <button className="notice-action" type="button" onClick={() => void loadCapsule(selectedProject.project_id)}><RefreshCw size={12} /> Retry capsule</button>
                    </div>
                  ) : null}
                  {capsule ? (
                    <>
                      <div className="project-capsule-meta">
                        <span><strong>{itemCountLabel(selectedProject.item_count)}</strong> in project</span>
                        <span><strong>{itemCountLabel(capsuleItemCount(capsule))}</strong> usable in capsule</span>
                        <span>Budget <strong>{capsule.item_budget} items</strong></span>
                      </div>
                      {capsule.truncated || capsule.omitted_count > 0 ? (
                        <div className="project-capsule-notice" role="status">
                          <strong>Bounded capsule.</strong> {capsule.omitted_count > 0 ? `${capsule.omitted_count} ${capsule.omitted_count === 1 ? "item was" : "items were"} omitted` : "Some context was bounded"}.
                          {capsule.omissions.length > 0 ? <span> {capsule.omissions.map((omission) => `${omission.count} by ${omission.reason === "character_budget" ? "character budget" : "item budget"}`).join(" · ")}.</span> : null}
                        </div>
                      ) : null}
                      {SECTION_LABELS.every((section) => capsule.sections[section.key].length === 0) ? (
                        <div className="project-capsule-no-context"><strong>No usable context for this project</strong><p>Core returned a resolved project, but no human-readable continuity items were available.</p></div>
                      ) : (
                        <div className="project-capsule-sections">
                          {SECTION_LABELS.map((section) => <CapsuleSection key={section.key} label={section.label} items={capsule.sections[section.key]} />)}
                        </div>
                      )}
                    </>
                  ) : null}
                </>
              )}
            </div>
          </div>
        </>
      ) : null}
    </section>
  );
}

export default ProjectContinuity;
