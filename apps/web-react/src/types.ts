export type OutputType = "docx" | "xlsx" | "pptx";
export type OversightMode = "auto" | "manual";

export interface TeamRun {
  id: string;
  title: string;
  status: string;
  plan_status?: string;
  oversight_mode?: OversightMode;
  output_type?: OutputType;
  auto_review_max_rounds?: number;
  selected_agents?: string[];
  requested_by?: string;
  request_text?: string;
}

export interface ConversationMessage {
  id: string;
  message_type: string;
  speaker_role?: string | null;
  speaker_identity?: string | null;
  visible_message?: string | null;
  raw_text?: string | null;
  created_at?: string;
}

export interface TeamActivity {
  id: string;
  summary: string;
  actor_handle?: string | null;
  target_handle?: string | null;
  created_at?: string;
}

export interface DeliverableItem {
  content?: string;
  created_at?: string;
  created_by_handle?: string;
}

export interface TeamTask {
  id: string;
  title: string;
  description?: string;
  owner_handle?: string;
  status?: string;
  claim_status?: string;
  priority?: number;
  artifact_goal?: string;
  review_required?: boolean;
  depends_on_task_ids?: string[];
  depends_on_titles?: string[];
  latest_activity_type?: string;
  latest_activity_at?: string;
  latest_artifact_type?: string | null;
  latest_artifact_created_at?: string | null;
  claimed_by_handle?: string | null;
  ready?: boolean;
  artifact_count?: number;
  review_state?: string;
  has_review_notes?: boolean;
}

export interface TeamMessage {
  id: string;
  visible_message?: string | null;
  raw_text?: string | null;
  speaker_role?: string | null;
  created_at?: string;
  message_type?: string;
}

export interface TeamBoardSnapshot {
  run: TeamRun;
  items: ConversationMessage[];
  activity: TeamActivity[];
  deliverable?: DeliverableItem | null;
  tasks?: TeamTask[];
  messages?: TeamMessage[];
}

export interface FileUploadItem {
  id: string;
  original_name: string;
}

export interface TeamRunSnapshot {
  id: string;
  title: string;
  status: string;
  plan_status?: string;
  selected_agents?: string[];
}
