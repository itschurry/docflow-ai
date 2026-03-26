import type { OutputType, OversightMode, ReviewMode, TeamBoardSnapshot, TeamRun, KnowledgeFile, ChunkItem, ReferenceMode, StyleMode, StyleStrength, JobDetail, JobStep } from "./types";

const JSON_HEADERS = { "Content-Type": "application/json" };

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const payload = await response.json();
      detail = typeof payload?.detail === "string" ? payload.detail : JSON.stringify(payload?.detail || payload);
    } catch {
      // keep statusText
    }
    throw new Error(`${response.status}: ${detail}`);
  }
  return response.json() as Promise<T>;
}

export async function listTeamRuns(): Promise<TeamRun[]> {
  const data = await request<{ items: TeamRun[] }>("/web/team-runs");
  return data.items || [];
}

export async function createTeamRun(payload: {
  requestedBy: string;
  oversightMode: OversightMode;
  outputType: OutputType;
  reviewMode: ReviewMode;
}): Promise<TeamBoardSnapshot> {
  return request<TeamBoardSnapshot>("/web/team-runs", {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify({
      title: "Web Team Run",
      requested_by: payload.requestedBy,
      oversight_mode: payload.oversightMode,
      output_type: payload.outputType,
      review_mode: payload.reviewMode,
      selected_agents: ["planner", "writer", "critic", "manager", "qa"],
    }),
  });
}

export function getBoard(runId: string): Promise<TeamBoardSnapshot> {
  return request<TeamBoardSnapshot>(`/web/team-runs/${runId}/board`);
}

export async function listKnowledgeFiles(): Promise<KnowledgeFile[]> {
  const data = await request<{ items: KnowledgeFile[] }>("/web/knowledge");
  return data.items || [];
}

export async function getFileChunks(fileId: string): Promise<ChunkItem[]> {
  const data = await request<{ items: ChunkItem[] }>(`/web/knowledge/${fileId}/chunks`);
  return data.items || [];
}

export function sendRequest(
  runId: string,
  payload: {
    text: string;
    senderName: string;
    outputType: OutputType;
    reviewMode: ReviewMode;
    sourceFileIds: string[];
    referenceMode?: ReferenceMode;
    styleMode?: StyleMode;
    styleStrength?: StyleStrength;
  },
): Promise<TeamBoardSnapshot> {
  return request<TeamBoardSnapshot>(`/web/team-runs/${runId}/requests`, {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify({
      text: payload.text,
      sender_name: payload.senderName,
      output_type: payload.outputType,
      review_mode: payload.reviewMode,
      source_file_ids: payload.sourceFileIds,
      reference_mode: payload.referenceMode ?? "auto",
      style_mode: payload.styleMode ?? "default",
      style_strength: payload.styleStrength ?? "medium",
    }),
  });
}

export async function uploadFile(file: File): Promise<{ id: string; original_name: string }> {
  const body = new FormData();
  body.append("uploaded_file", file);
  return request<{ id: string; original_name: string }>("/web/files", {
    method: "POST",
    body,
  });
}

export async function exportDeliverable(
  runId: string,
  format: OutputType,
): Promise<{ download_path: string }> {
  return request<{ download_path: string }>(`/web/team-runs/${runId}/exports`, {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify({ format }),
  });
}

export function approvePlan(runId: string): Promise<TeamBoardSnapshot> {
  return request<TeamBoardSnapshot>(`/web/team-runs/${runId}/plan/approve`, {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify({}),
  });
}

export function rejectPlan(runId: string, reason?: string): Promise<TeamBoardSnapshot> {
  return request<TeamBoardSnapshot>(`/web/team-runs/${runId}/plan/reject`, {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify({ reason }),
  });
}

export function updateTask(taskId: string, payload: Record<string, unknown>): Promise<TeamBoardSnapshot> {
  return request<TeamBoardSnapshot>(`/web/tasks/${taskId}`, {
    method: "PATCH",
    headers: JSON_HEADERS,
    body: JSON.stringify(payload),
  });
}

export async function listAgents(): Promise<{ agents: { handle: string; display_name?: string }[] }> {
  return request<{ agents: { handle: string; display_name?: string }[] }>("/web/agents");
}

export async function updateRunAgents(runId: string, selectedAgents: string[]): Promise<TeamBoardSnapshot> {
  await request<{ ok: boolean }>(`/web/team-runs/${runId}/agents`, {
    method: "PUT",
    headers: JSON_HEADERS,
    body: JSON.stringify({ selected_agents: selectedAgents }),
  });
  return getBoard(runId);
}

export function createTask(
  runId: string,
  payload: {
    title: string;
    description: string;
    ownerHandle: string;
    artifactGoal: string;
    priority: number;
    reviewRequired: boolean;
  },
): Promise<TeamBoardSnapshot> {
  return request<TeamBoardSnapshot>(`/web/team-runs/${runId}/tasks`, {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify({
      title: payload.title,
      description: payload.description,
      owner_handle: payload.ownerHandle,
      artifact_goal: payload.artifactGoal,
      priority: payload.priority,
      review_required: payload.reviewRequired,
    }),
  });
}

export async function deleteKnowledgeFile(fileId: string): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>(`/web/knowledge/${fileId}`, { method: "DELETE" });
}

export async function deleteTeamRun(runId: string): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>(`/web/team-runs/${runId}`, {
    method: "DELETE",
  });
}

export function cancelTeamRun(runId: string): Promise<TeamBoardSnapshot> {
  return request<TeamBoardSnapshot>(`/web/team-runs/${runId}/cancel`, { method: "POST" });
}

export function getJobDetail(jobId: string): Promise<JobDetail> {
  return request<JobDetail>(`/api/jobs/${jobId}`);
}

export function getJobSteps(jobId: string): Promise<JobStep[]> {
  return request<JobStep[]>(`/api/jobs/${jobId}/steps`);
}
