import type { OutputType, OversightMode, TeamBoardSnapshot, TeamRun } from "./types";

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
  autoReviewMaxRounds: number;
}): Promise<TeamBoardSnapshot> {
  return request<TeamBoardSnapshot>("/web/team-runs", {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify({
      title: "Web Team Run",
      requested_by: payload.requestedBy,
      oversight_mode: payload.oversightMode,
      output_type: payload.outputType,
      auto_review_max_rounds: payload.autoReviewMaxRounds,
      selected_agents: ["planner", "writer", "critic", "manager", "qa"],
    }),
  });
}

export function getBoard(runId: string): Promise<TeamBoardSnapshot> {
  return request<TeamBoardSnapshot>(`/web/team-runs/${runId}/board`);
}

export function sendRequest(
  runId: string,
  payload: { text: string; senderName: string; outputType: OutputType; autoReviewMaxRounds: number; sourceFileIds: string[] },
): Promise<TeamBoardSnapshot> {
  return request<TeamBoardSnapshot>(`/web/team-runs/${runId}/requests`, {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify({
      text: payload.text,
      sender_name: payload.senderName,
      output_type: payload.outputType,
      auto_review_max_rounds: payload.autoReviewMaxRounds,
      source_file_ids: payload.sourceFileIds,
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
