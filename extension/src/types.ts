/** Canonical TypeScript contracts shared across Extension modules and Server REST payloads. */

export type LinkType = "magnet" | "hoster" | "torrent";

export type JobStatus =
  | "queued"
  | "resolving_debrid"
  | "downloading"
  | "extracting"
  | "completed"
  | "failed";

export type PopupTab = "jobs" | "add" | "settings";
export type QueueScope = "browser" | "all";
export type StatusFilter = "active" | "completed" | "failed" | null;
export type StatusMessageType = "info" | "success" | "error";

export interface DownloadJob {
  job_id: string;
  source_url: string;
  link_type: LinkType | string;
  status: JobStatus | string;
  resolved_urls?: string[];
  output_dir?: string;
  filenames?: string[];
  extracted_files?: string[];
  total_files?: number;
  completed_files?: number;
  progress_percent?: number;
  download_speed?: string | null;
  error?: string | null;
  category?: string | null;
  source?: string | null;
  client_id?: string | null;
  client_name?: string | null;
  info_hash?: string | null;
  created_at?: string;
  updated_at?: string;
  completed_at?: string | null;
}

export interface DownloadRequest {
  url: string;
  apiKey?: string;
  subfolder?: string;
  category?: string;
  source?: string;
  client_id?: string;
  client_name?: string;
}

export interface DownloadResponse {
  job_id: string;
  status: JobStatus | string;
  link_type: LinkType | string;
  message: string;
}

export interface ServerProfile {
  id: string;
  name: string;
  url: string;
  apiKey: string;
}

export interface ExtensionSettings {
  endpoints: ServerProfile[];
  activeEndpointId: string;
  serverEndpoint: string;
  nasEndpoint: string;
  apiKey: string;
  enableNotifications: boolean;
  clientId: string;
  clientName: string;
  queueScope: QueueScope;
}

export interface ClientIdentity {
  clientId: string;
  clientName: string;
}

export interface QueueErrorItem {
  url?: string;
  error: string;
}

export interface QueueUrlsResponse {
  ok: boolean;
  queued: number;
  errors: QueueErrorItem[];
}

export interface FirefoxPermissionInfo {
  missing: boolean;
  originPattern: string | null;
  hostname: string;
}

export type RuntimeMessage =
  | { type: "QUEUE_URLS"; urls: string[]; subfolder?: string; source?: string }
  | { type: "UPDATE_BADGE" }
  | { type: "GET_SESSION_TAB" }
  | { type: "SET_SESSION_TAB"; tab: PopupTab }
  | { type: "ENSURE_CLIENT_IDENTITY"; isBrave?: boolean };

export interface SettingsResponse {
  debrid_provider: string;
  rd_api_key_masked: string;
  rd_base_url: string;
  download_dir: string;
  aria2c_max_connections: number;
  aria2c_split: number;
  max_concurrent_downloads: number;
  auto_extract: boolean;
  cleanup_after_extract: boolean;
  supported_providers?: string[];
}

export interface SettingsUpdateRequest {
  debrid_provider?: string;
  rd_api_key?: string;
  rd_base_url?: string;
  download_dir?: string;
  aria2c_max_connections?: number;
  aria2c_split?: number;
  max_concurrent_downloads?: number;
  auto_extract?: boolean;
  cleanup_after_extract?: boolean;
}

export interface TestDebridRequest {
  provider?: string;
  apiKey?: string;
}

export interface TestDebridResponse {
  ok: boolean;
  provider: string;
  username?: string | null;
  account_type?: string | null;
  expiration?: string | null;
  message?: string | null;
}

