"use client";

import { EraserIcon, FolderOpenIcon, RefreshCwIcon, UsbIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { Drive, DrivesResponse, Settings } from "@/lib/api";
import { SimpleSelect, type Option } from "./simple-select";

const FORMATS: Option[] = [
  { value: "mp3", label: "MP3" },
  { value: "m4a", label: "M4A" },
  { value: "opus", label: "Opus" },
  { value: "ogg", label: "OGG" },
  { value: "flac", label: "FLAC" },
  { value: "wav", label: "WAV" },
];

export function DestinationBar({
  drives,
  destination,
  onDestination,
  settings,
  onSettings,
  canFormat,
  onBrowse,
  onRefresh,
  onFormatDrive,
  refreshing,
}: {
  drives: DrivesResponse | null;
  destination: string;
  onDestination: (path: string) => void;
  settings: Settings | null;
  onSettings: (partial: Partial<Settings>) => void;
  canFormat: boolean;
  onBrowse: () => void;
  onRefresh: () => void;
  onFormatDrive: (drive: Drive) => void;
  refreshing: boolean;
}) {
  const list = drives?.drives ?? [];
  const options: Option[] = list.map((drive) => ({
    value: drive.path,
    label: (
      <span className="flex min-w-0 items-center gap-2">
        <UsbIcon className="size-4 text-brand" />
        <span className="truncate">{drive.label || drive.path}</span>
        <span className="text-xs text-muted-foreground">{drive.free_human} free</span>
      </span>
    ),
  }));
  const folderOption = (path: string) => ({
    value: path,
    label: (
      <span className="flex min-w-0 items-center gap-2">
        <FolderOpenIcon className="size-4 text-muted-foreground" />
        <span className="truncate">{path}</span>
      </span>
    ),
  });
  if (drives?.fallback && !options.some((o) => o.value === drives.fallback)) options.push(folderOption(drives.fallback));
  if (destination && !options.some((o) => o.value === destination)) options.unshift(folderOption(destination));

  const drive = list.find((d) => d.path === destination);
  const lossless = settings && ["flac", "wav"].includes(settings.format);

  return (
    <div className="grid gap-3 rounded-xl border bg-card p-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-end">
      <div className="min-w-0 space-y-1.5">
        <Label className="text-xs text-muted-foreground" htmlFor="destination">
          Save to
        </Label>
        <div className="flex gap-1.5">
          <SimpleSelect
            id="destination"
            value={destination}
            onChange={onDestination}
            options={options}
            placeholder="Choose a drive or folder"
            className="min-w-0 flex-1"
          />
          <Tooltip>
            <TooltipTrigger render={<Button variant="outline" size="icon" onClick={onBrowse} aria-label="Browse folders" />}>
              <FolderOpenIcon />
            </TooltipTrigger>
            <TooltipContent>Pick another folder</TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger render={<Button variant="outline" size="icon" onClick={onRefresh} aria-label="Rescan drives" />}>
              <RefreshCwIcon className={refreshing ? "animate-spin" : ""} />
            </TooltipTrigger>
            <TooltipContent>Rescan for drives</TooltipContent>
          </Tooltip>
          {canFormat && drive && (
            <Tooltip>
              <TooltipTrigger
                render={<Button variant="outline" size="icon" onClick={() => onFormatDrive(drive)} aria-label="Format drive" />}
              >
                <EraserIcon />
              </TooltipTrigger>
              <TooltipContent>Erase and format for the car ({drive.fstype})</TooltipContent>
            </Tooltip>
          )}
        </div>
      </div>
      <div className="grid grid-cols-2 gap-1.5 sm:w-64">
        <div className="space-y-1.5">
          <Label className="text-xs text-muted-foreground">Format</Label>
          <SimpleSelect
            value={settings?.format ?? "mp3"}
            onChange={(format) => onSettings({ format })}
            options={FORMATS}
            disabled={!settings}
          />
        </div>
        <div className="space-y-1.5">
          <Label className="text-xs text-muted-foreground">Quality</Label>
          <SimpleSelect
            value={lossless ? "lossless" : (settings?.quality ?? "320")}
            onChange={(quality) => onSettings({ quality })}
            options={
              lossless
                ? [{ value: "lossless", label: "Lossless" }]
                : ["320", "256", "192", "128"].map((q) => ({ value: q, label: `${q} kbps` }))
            }
            disabled={!settings || Boolean(lossless)}
          />
        </div>
      </div>
    </div>
  );
}
