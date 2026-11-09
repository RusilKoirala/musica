"use client";

import { useCallback, useEffect, useState } from "react";
import { ArrowUpIcon, FolderIcon, FolderPlusIcon, Loader2Icon } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { post, type FolderListing } from "@/lib/api";

export function FolderDialog({
  open,
  onOpenChange,
  start,
  onPick,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  start: string;
  onPick: (path: string) => void;
}) {
  const [listing, setListing] = useState<FolderListing | null>(null);
  const [loading, setLoading] = useState(false);
  const [newName, setNewName] = useState("");

  const go = useCallback(async (path: string | null) => {
    setLoading(true);
    try {
      setListing(await post<FolderListing>("/api/folders", { path }));
    } catch (exc) {
      setListing((current) => (current ? { ...current, error: (exc as Error).message } : null));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (open) {
      setNewName("");
      void go(start || null);
    }
  }, [open, start, go]);

  const target = listing ? (newName.trim() ? `${listing.path.replace(/\/$/, "")}/${newName.trim()}` : listing.path) : "";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Choose a folder</DialogTitle>
          <DialogDescription className="truncate font-mono text-xs">{listing?.path ?? "…"}</DialogDescription>
        </DialogHeader>

        <div className="overflow-hidden rounded-lg border">
          <button
            type="button"
            disabled={!listing?.parent || loading}
            onClick={() => listing?.parent && go(listing.parent)}
            className="flex w-full items-center gap-2 border-b px-3 py-2 text-sm text-muted-foreground hover:bg-muted disabled:opacity-40"
          >
            <ArrowUpIcon className="size-4" /> Up one level
            {loading && <Loader2Icon className="ml-auto size-4 animate-spin" />}
          </button>
          <ul className="h-64 overflow-y-auto">
            {listing?.error && <li className="px-3 py-2 text-sm text-destructive">{listing.error}</li>}
            {listing && !listing.dirs.length && !listing.error && (
              <li className="px-3 py-6 text-center text-sm text-muted-foreground">No folders here</li>
            )}
            {listing?.dirs.map((dir) => (
              <li key={dir.path}>
                <button
                  type="button"
                  onClick={() => go(dir.path)}
                  className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm hover:bg-muted"
                >
                  <FolderIcon className="size-4 shrink-0 text-brand" />
                  <span className="truncate">{dir.name}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>

        <div className="flex items-center gap-2">
          <FolderPlusIcon className="size-4 shrink-0 text-muted-foreground" />
          <Input placeholder="New subfolder (optional)" value={newName} onChange={(e) => setNewName(e.target.value)} />
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            disabled={!listing || Boolean(listing.error)}
            onClick={() => {
              onPick(target);
              onOpenChange(false);
            }}
          >
            Use this folder
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
