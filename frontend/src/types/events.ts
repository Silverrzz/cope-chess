import type { ChatMessage, ChatSettings, Identifier } from "@/components/public/types";

export type EventStatus =
  | "draft"
  | "announced"
  | "scheduled"
  | "live"
  | "intermission"
  | "postponed"
  | "completed"
  | "cancelled";

export interface EventRecord {
  id: number;
  slug: string;
  handler_key: string;
  handler_version: number;
  title: string;
  subtitle: string;
  summary: string;
  description: string;
  rules: string;
  status: EventStatus;
  featured: boolean;
  published_at: string | null;
  scheduled_start_at: string | null;
  scheduled_end_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  theme: Record<string, unknown>;
  config?: Record<string, unknown>;
  state?: Record<string, unknown>;
  revision: number;
  created_at: string;
  updated_at: string;
}

export interface EventHandlerState {
  key: string;
  label: string;
  required_version: number;
  installed_version: number | null;
  available: boolean;
  current: boolean;
}

export interface EventCounts {
  stages: number;
  sessions: number;
  cast: number;
  contests: number;
  updates: number;
  awards: number;
}

export interface EventStage {
  id: number;
  event_id: number;
  stage_key: string;
  title: string;
  summary: string;
  status: string;
  position: number;
  scheduled_start_at: string | null;
  scheduled_end_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  metadata?: Record<string, unknown>;
}

export interface EventSession {
  id: number;
  event_id: number;
  stage_id: number | null;
  session_key: string;
  title: string;
  summary: string;
  status: string;
  position: number;
  scheduled_start_at: string | null;
  scheduled_end_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  metadata?: Record<string, unknown>;
}

export interface EventCastMember {
  id: number;
  event_id: number;
  parent_id: number | null;
  member_key: string;
  kind: string;
  display_name: string;
  short_name: string;
  role: string;
  status: string;
  engine_version_id: Identifier | null;
  profile: string;
  avatar_url: string;
  accent_color: string;
  position: number;
  metadata?: Record<string, unknown>;
}

export interface EventContest {
  id: number;
  event_id: number;
  stage_id: number | null;
  session_id: number | null;
  contest_key: string;
  title: string;
  summary: string;
  status: string;
  position: number;
  scheduled_start_at: string | null;
  scheduled_end_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  result: string;
  state?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
}

export interface EventContestCast {
  contest_id: number;
  cast_member_id: number;
  side: string;
  role: string;
  position: number;
  metadata?: Record<string, unknown>;
}

export interface EventUpdate {
  id: number;
  event_id: number;
  kind: string;
  title: string;
  body: string;
  pinned: boolean;
  occurred_at: string;
  published_at: string | null;
  created_at: string;
}

export interface EventAward {
  id: number;
  event_id: number;
  award_key: string;
  title: string;
  description: string;
  recipient_cast_id: number | null;
  recipient_label: string;
  position: number;
  awarded_at: string | null;
  metadata?: Record<string, unknown>;
}

export interface EventSummary {
  record: EventRecord;
  counts: EventCounts;
  next_session: EventSession | null;
  handler: EventHandlerState;
}

export interface EventDetailResponse {
  server_time: string;
  event: EventRecord;
  handler: EventHandlerState;
  stages: EventStage[];
  sessions: EventSession[];
  cast: EventCastMember[];
  contests: EventContest[];
  contest_cast: EventContestCast[];
  updates: EventUpdate[];
  awards: EventAward[];
  counts: EventCounts;
  chat_messages: ChatMessage[];
  chat_settings: ChatSettings;
  custom: unknown;
  spectator_count?: number;
}

export interface CurrentEventResponse {
  event: EventSummary | null;
}

export interface PublicEventListResponse {
  server_time: string;
  current: EventSummary | null;
  events: EventSummary[];
}

export interface AdminEventListResponse {
  events: EventSummary[];
  statuses: EventStatus[];
  registered_modules: Array<{ key: string; label: string; version: number }>;
}
