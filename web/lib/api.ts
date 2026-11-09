// Thin client for the FastAPI backend. Same origin in production; proxied in dev.
// Set NEXT_PUBLIC_API_URL at build time to talk to a backend on another host.

export const API_BASE = (process.env.NEXT_PUBLIC_API_URL ?? "").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

export async function api<T = unknown>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    credentials: "include",
    ...init,
    headers: { "Content-Type": "application/json", ...(init.headers ?? {}) },
  });
  const text = await response.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = { detail: text };
  }
  if (!response.ok) {
    const detail = (body as { detail?: unknown })?.detail;
    const message =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((d) => d?.msg).filter(Boolean).join(", ")
          : `Request failed (${response.status})`;
    if (response.status === 401) window.dispatchEvent(new Event("musica:unauthorized"));
    throw new ApiError(message, response.status);
  }
  return body as T;
}

export const post = <T = unknown>(path: string, body?: unknown) =>
  api<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export const put = <T = unknown>(path: string, body?: unknown) =>
  api<T>(path, { method: "PUT", body: JSON.stringify(body) });

export const fileUrl = (path: string) => `${API_BASE}/api/file?path=${encodeURIComponent(path)}`;
export const eventsUrl = () => `${API_BASE}/api/events`;

// ------------------------------------------------------------------ types

export type Health = {
  version: string;
  os: string;
  ytdlp: string;
  ffmpeg: { ok: boolean; version: string | null };
  js_runtime: string | null;
  python: string;
  auth: boolean;
  demo: boolean;
  demo_limit: number;
  can_format: boolean;
  root: string | null;
};

export type Settings = {
  format: string;
  quality: string;
  destination: string;
  playlist_subfolders: boolean;
  naming: "artist_title" | "title";
  skip_name_duplicates: boolean;
  archive_on_drive: boolean;
  stage_locally: boolean;
  search_results: number;
  concurrency: number;
  cookiefile: string;
  car_mode: boolean;
  car_genre: string;
  number_files: boolean;
};

export type SettingsResponse = { settings: Settings; codecs: string[]; bitrates: string[] };

export type Drive = {
  path: string;
  label: string;
  device: string;
  fstype: string;
  free_human: string;
  total_human: string;
  writeable: boolean;
  kind: string;
};

export type DrivesResponse = { os: string; drives: Drive[]; fallback: string };

export type Item = {
  id: string;
  url: string;
  title: string;
  artist: string;
  duration: number | null;
  thumbnail: string | null;
  playlist: string | null;
  playlist_index: number | null;
  source?: string;
  album?: string;
  in_archive?: boolean;
  name_duplicate?: unknown;
  duplicate_display?: string;
};

export type ResolveResponse = {
  kind: string;
  query: string;
  url?: string | null;
  playlist_title: string | null;
  is_playlist?: boolean;
  destination: string;
  count: number;
  items: Item[];
  scanned_files: number;
};

export type TrackStatus = "pending" | "running" | "done" | "skipped" | "error" | "cancelled";

export type Track = {
  key: string;
  title: string;
  artist: string;
  thumbnail: string | null;
  duration: number | null;
  status: TrackStatus;
  percent: number;
  speed: number | null;
  eta: number | null;
  stage: string | null;
  error: string | null;
  skip_reason: string | null;
  path: string | null;
  size: number | null;
};

export type Job = {
  id: string;
  destination: string;
  created_at: number;
  label: string;
  status: "running" | "queued" | "idle" | "finished" | "empty" | "cancelling" | "cancelled";
  total: number;
  remaining: number;
  processed: number;
  counts: Record<TrackStatus, number>;
  bytes_done: number;
  tracks: Track[];
};

export type QueueState = {
  paused: boolean;
  active_job_id: string | null;
  jobs: Job[];
  counts: Record<TrackStatus, number>;
};

export type LogEntry = { at: number; level: "info" | "ok" | "warn" | "error"; text: string };

export type FolderListing = {
  path: string;
  parent: string | null;
  dirs: { name: string; path: string }[];
  error: string | null;
};

export const itemKey = (item: Item) => item.url || item.id;
