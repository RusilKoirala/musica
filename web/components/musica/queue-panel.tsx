"use client";

import { useState } from "react";
import {
  CheckIcon,
  ChevronDownIcon,
  CircleDashedIcon,
  CircleSlashIcon,
  InboxIcon,
  Loader2Icon,
  PauseIcon,
  PlayIcon,
  RotateCwIcon,
  SkipForwardIcon,
  SquareIcon,
  TerminalIcon,
  XIcon,
} from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { post, type Job, type LogEntry, type QueueState, type Track } from "@/lib/api";
import { bytes, clock, plural, speed } from "@/lib/format";
import { cn } from "@/lib/utils";
import { PlayButton, SaveButton } from "./player";
import { Cover } from "./results";

async function act(path: string, body: unknown, message?: string) {
  try {
    await post(path, body);
    if (message) toast(message);
  } catch (exc) {
    toast.error((exc as Error).message);
  }
}

function StatusIcon({ status }: { status: Track["status"] }) {
  const base = "size-4 shrink-0";
  switch (status) {
    case "running":
      return <Loader2Icon className={cn(base, "animate-spin text-brand")} />;
    case "done":
      return <CheckIcon className={cn(base, "text-success")} />;
    case "skipped":
      return <SkipForwardIcon className={cn(base, "text-muted-foreground")} />;
    case "error":
      return <XIcon className={cn(base, "text-destructive")} />;
    case "cancelled":
      return <CircleSlashIcon className={cn(base, "text-muted-foreground")} />;
    default:
      return <CircleDashedIcon className={cn(base, "text-muted-foreground/60")} />;
  }
}

function TrackRow({ track }: { track: Track }) {
  const detail =
    track.status === "error"
      ? track.error
      : track.status === "running"
        ? [track.stage, speed(track.speed), track.eta ? `${track.eta}s left` : ""].filter(Boolean).join(" · ")
        : track.status === "done"
          ? bytes(track.size)
          : track.stage;

  return (
    <li className="group relative flex items-center gap-3 px-4 py-2">
      {track.status === "running" && (
        <div
          className="absolute inset-y-0 left-0 bg-brand/8 transition-[width] duration-300"
          style={{ width: `${Math.min(100, track.percent)}%` }}
        />
      )}
      <StatusIcon status={track.status} />
      <Cover src={track.thumbnail} className="size-8" />
      <div className="relative min-w-0 flex-1">
        <p className={cn("truncate text-sm", track.status === "skipped" && "text-muted-foreground")}>{track.title}</p>
        {detail && (
          <p className={cn("truncate text-xs text-muted-foreground", track.status === "error" && "text-destructive")}>
            {detail}
          </p>
        )}
      </div>
      {track.status === "running" && (
        <span className="tabular relative text-xs text-muted-foreground">{Math.round(track.percent)}%</span>
      )}
      {(track.status === "done" || track.status === "skipped") && track.path && (
        <>
          <PlayButton song={{ path: track.path, title: track.title }} />
          <SaveButton path={track.path} />
        </>
      )}
      {(track.status === "error" || track.status === "cancelled") && (
        <Button
          variant="ghost"
          size="xs"
          className="relative"
          onClick={() => act("/api/queue/retry", { keys: [track.key], force: true }, "Retrying")}
        >
          <RotateCwIcon /> Retry
        </Button>
      )}
    </li>
  );
}

const STATUS_LABEL: Record<Job["status"], string> = {
  running: "Downloading",
  queued: "Queued",
  idle: "Waiting",
  finished: "Done",
  empty: "Empty",
  cancelling: "Stopping",
  cancelled: "Stopped",
};

function JobCard({ job, defaultOpen }: { job: Job; defaultOpen: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  const value = job.total ? (job.processed / job.total) * 100 : 0;
  const failed = job.tracks.filter((t) => t.status === "error");
  const active = job.status === "running" || job.status === "queued";

  return (
    <div className="border-b last:border-0">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-muted/40"
      >
        <div className="min-w-0 flex-1 space-y-1.5">
          <div className="flex items-center gap-2">
            <p className="truncate text-sm font-medium">{job.label || "Download"}</p>
            <Badge variant={active ? "default" : "secondary"} className={cn(active && "bg-brand text-brand-foreground")}>
              {STATUS_LABEL[job.status]}
            </Badge>
          </div>
          <div className="h-1 overflow-hidden rounded-full bg-muted">
            <div
              className={cn("h-full rounded-full transition-[width] duration-500", active ? "bg-brand" : "bg-success")}
              style={{ width: `${value}%` }}
            />
          </div>
          <p className="tabular text-xs text-muted-foreground">
            {job.processed}/{job.total}
            {job.counts.done > 0 && ` · ${job.counts.done} saved`}
            {job.counts.skipped > 0 && ` · ${job.counts.skipped} skipped`}
            {job.counts.error > 0 && <span className="text-destructive"> · {job.counts.error} failed</span>}
            {job.bytes_done > 0 && ` · ${bytes(job.bytes_done)}`}
          </p>
        </div>
        <ChevronDownIcon className={cn("size-4 shrink-0 text-muted-foreground transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <>
          <ul className="divide-y border-t bg-muted/20">
            {job.tracks.map((track) => (
              <TrackRow key={track.key} track={track} />
            ))}
          </ul>
          {failed.length > 1 && (
            <div className="border-t px-4 py-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() =>
                  act("/api/queue/retry", { keys: failed.map((t) => t.key), force: true }, `Retrying ${failed.length}`)
                }
              >
                <RotateCwIcon /> Retry {failed.length} failed
              </Button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function ActivityLog({ logs }: { logs: LogEntry[] }) {
  const [open, setOpen] = useState(false);
  const last = logs[logs.length - 1];
  return (
    <Card className="gap-0 p-0">
      <button type="button" onClick={() => setOpen(!open)} className="flex w-full items-center gap-2 px-4 py-3 text-left">
        <TerminalIcon className="size-4 text-muted-foreground" />
        <span className="text-sm font-medium">Activity</span>
        {!open && last && <span className="ml-2 min-w-0 flex-1 truncate text-xs text-muted-foreground">{last.text}</span>}
        <ChevronDownIcon className={cn("ml-auto size-4 text-muted-foreground transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <ol className="max-h-64 overflow-y-auto border-t px-4 py-2 font-mono text-xs leading-relaxed">
          {logs.length === 0 && <li className="text-muted-foreground">Nothing yet.</li>}
          {[...logs].reverse().map((entry, i) => (
            <li key={`${entry.at}-${i}`} className="flex gap-3">
              <span className="shrink-0 text-muted-foreground">{clock(entry.at)}</span>
              <span
                className={cn(
                  entry.level === "error" && "text-destructive",
                  entry.level === "warn" && "text-brand",
                  entry.level === "ok" && "text-success",
                )}
              >
                {entry.text}
              </span>
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}

export function QueuePanel({ queue, logs, connected }: { queue: QueueState | null; logs: LogEntry[]; connected: boolean }) {
  const jobs = queue?.jobs ?? [];
  const busy = jobs.some((job) => ["running", "queued", "cancelling"].includes(job.status));
  const finished = jobs.some((job) => ["finished", "cancelled", "empty"].includes(job.status));

  return (
    <div className="space-y-4">
      <Card className="gap-0 overflow-hidden p-0">
        <div className="flex items-center gap-2 border-b px-4 py-3">
          <h2 className="font-medium">Queue</h2>
          <span
            className={cn("size-1.5 rounded-full", connected ? "bg-success" : "bg-muted-foreground/40")}
            title={connected ? "Live" : "Reconnecting"}
          />
          {queue && queue.counts.pending + queue.counts.running > 0 && (
            <span className="tabular text-sm text-muted-foreground">
              {plural(queue.counts.pending + queue.counts.running, "song")} left
            </span>
          )}
          <div className="ml-auto flex gap-1">
            {busy && (
              <>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => act("/api/queue/pause", { paused: !queue?.paused })}
                >
                  {queue?.paused ? <PlayIcon /> : <PauseIcon />}
                  {queue?.paused ? "Resume" : "Pause"}
                </Button>
                <Button variant="ghost" size="sm" onClick={() => act("/api/queue/cancel", {}, "Stopping the queue")}>
                  <SquareIcon /> Stop
                </Button>
              </>
            )}
            {finished && (
              <Button variant="ghost" size="sm" onClick={() => act("/api/queue/clear", {})}>
                Clear
              </Button>
            )}
          </div>
        </div>
        {queue?.paused && (
          <p className="border-b bg-brand/10 px-4 py-2 text-xs text-foreground">Paused. The current song finishes first.</p>
        )}
        {jobs.length === 0 ? (
          <Empty className="py-10">
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <InboxIcon />
              </EmptyMedia>
              <EmptyTitle>No downloads yet</EmptyTitle>
              <EmptyDescription>Find something, tick what you want and press download.</EmptyDescription>
            </EmptyHeader>
          </Empty>
        ) : (
          <div className="max-h-[70vh] overflow-y-auto">
            {jobs.map((job, index) => (
              <JobCard key={job.id} job={job} defaultOpen={index === 0} />
            ))}
          </div>
        )}
      </Card>
      <ActivityLog logs={logs} />
    </div>
  );
}
