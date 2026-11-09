"use client";

import { DownloadIcon, ListMusicIcon, Loader2Icon, MusicIcon, SearchXIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { itemKey, type Item, type ResolveResponse } from "@/lib/api";
import { duration, plural } from "@/lib/format";
import { cn } from "@/lib/utils";

export const isOwned = (item: Item) => Boolean(item.in_archive || item.name_duplicate);

export function Cover({ src, className }: { src: string | null | undefined; className?: string }) {
  return (
    <div className={cn("relative shrink-0 overflow-hidden rounded-md bg-muted", className)}>
      <MusicIcon className="absolute inset-0 m-auto size-4 text-muted-foreground/60" />
      {src && (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={src}
          alt=""
          loading="lazy"
          referrerPolicy="no-referrer"
          className="relative size-full object-cover"
          onError={(e) => (e.currentTarget.style.display = "none")}
        />
      )}
    </div>
  );
}

export function ResultsSkeleton() {
  return (
    <Card className="gap-0 p-0">
      <div className="border-b p-4">
        <Skeleton className="h-5 w-48" />
      </div>
      {Array.from({ length: 5 }).map((_, i) => (
        <div key={i} className="flex items-center gap-3 border-b px-4 py-3 last:border-0">
          <Skeleton className="size-4 rounded" />
          <Skeleton className="size-11 rounded-md" />
          <div className="flex-1 space-y-2">
            <Skeleton className="h-4 w-2/3" />
            <Skeleton className="h-3 w-1/3" />
          </div>
        </div>
      ))}
    </Card>
  );
}

export function Results({
  preview,
  selected,
  onSelectedChange,
  onDownload,
  downloading,
  canDownload,
}: {
  preview: ResolveResponse;
  selected: Set<string>;
  onSelectedChange: (next: Set<string>) => void;
  onDownload: (force: boolean) => void;
  downloading: boolean;
  canDownload: boolean;
}) {
  const items = preview.items;
  const owned = items.filter(isOwned).length;
  const chosen = items.filter((item) => selected.has(itemKey(item)));
  // Picking only songs that are already there means "put them there again".
  const force = chosen.length > 0 && chosen.every(isOwned);
  const all = chosen.length === items.length && items.length > 0;

  if (!items.length) {
    return (
      <Card>
        <Empty>
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <SearchXIcon />
            </EmptyMedia>
            <EmptyTitle>Nothing found</EmptyTitle>
            <EmptyDescription>Try a different spelling, or paste the link directly.</EmptyDescription>
          </EmptyHeader>
        </Empty>
      </Card>
    );
  }

  const toggle = (item: Item, on: boolean) => {
    const next = new Set(selected);
    if (on) next.add(itemKey(item));
    else next.delete(itemKey(item));
    onSelectedChange(next);
  };

  return (
    <Card className="gap-0 overflow-hidden p-0">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b px-4 py-3">
        <Checkbox
          checked={all}
          indeterminate={!all && chosen.length > 0}
          onCheckedChange={(on) => onSelectedChange(new Set(on ? items.map(itemKey) : []))}
          aria-label="Select all"
        />
        <div className="flex min-w-0 flex-1 items-center gap-2">
          {preview.is_playlist && <ListMusicIcon className="size-4 shrink-0 text-brand" />}
          <h2 className="truncate font-medium">{preview.playlist_title || `Results for “${preview.query}”`}</h2>
        </div>
        <p className="text-sm text-muted-foreground">
          {plural(items.length, "song")}
          {owned > 0 && ` · ${owned} already there`}
        </p>
      </div>

      <ul className="max-h-[min(60vh,640px)] divide-y overflow-y-auto">
        {items.map((item) => {
          const key = itemKey(item);
          const on = selected.has(key);
          return (
            <li key={key}>
              <label
                className={cn(
                  "flex cursor-pointer items-center gap-3 px-4 py-2.5 transition-colors hover:bg-muted/50",
                  !on && "opacity-60",
                )}
              >
                <Checkbox checked={on} onCheckedChange={(next) => toggle(item, next)} />
                <Cover src={item.thumbnail} className="size-11" />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">
                    {item.playlist_index != null && preview.is_playlist && (
                      <span className="tabular mr-2 text-muted-foreground">{item.playlist_index}</span>
                    )}
                    {item.title}
                  </p>
                  <p className="truncate text-xs text-muted-foreground">
                    {[item.artist, item.album].filter(Boolean).join(" · ")}
                  </p>
                </div>
                {isOwned(item) && (
                  <Tooltip>
                    <TooltipTrigger render={<span />}>
                      <Badge variant="secondary" className="shrink-0">
                        {item.in_archive ? "On drive" : "Similar"}
                      </Badge>
                    </TooltipTrigger>
                    <TooltipContent>
                      {item.in_archive
                        ? "This exact video is already on the drive"
                        : `Looks like ${item.duplicate_display || "a file"} already on the drive`}
                    </TooltipContent>
                  </Tooltip>
                )}
                <span className="tabular w-12 shrink-0 text-right text-xs text-muted-foreground">
                  {duration(item.duration)}
                </span>
              </label>
            </li>
          );
        })}
      </ul>

      <div className="flex items-center justify-between gap-3 border-t bg-muted/30 px-4 py-3">
        <p className="text-sm text-muted-foreground">
          <span className="tabular font-medium text-foreground">{chosen.length}</span> selected
        </p>
        <Button size="lg" onClick={() => onDownload(force)} disabled={!chosen.length || downloading || !canDownload}>
          {downloading ? <Loader2Icon className="animate-spin" /> : <DownloadIcon />}
          {force ? "Download again" : `Download ${chosen.length || ""}`.trim()}
        </Button>
      </div>
    </Card>
  );
}
