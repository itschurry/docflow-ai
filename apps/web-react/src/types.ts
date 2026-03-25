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

export interface TeamBoardSnapshot {
  run: TeamRun;
  items: ConversationMessage[];
  activity: TeamActivity[];
  deliverable?: DeliverableItem | null;
}

export interface FileUploadItem {
  id: string;
  original_name: string;
}
