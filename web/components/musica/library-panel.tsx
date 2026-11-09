"use client";

import { useCallback, useEffect, useState } from "react";
import { LibraryIcon, MusicIcon, RefreshCwIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { api, type LibraryResponse } from "@/lib/api";
import { bytes } from "@/lib/format";
import { PlayButton, SaveButton } from "./player";

export function LibraryPanel({ refreshKey }: { refreshKey: number }) {
  const [library, setLibrary] = useState<LibraryResponse | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setLibrary(await api<LibraryResponse>("/api/library"));
    } catch {
      // The panel just stays as it was; the queue shows real errors.
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load, refreshKey]);

  const songs = library?.songs ?? [];

  return (
    <Card className="gap-0 overflow-hidden p-0">
      <div className="flex items-center gap-2 border-b px-4 py-3">
        <LibraryIcon className="size-4 text-muted-foreground" />
        <h2 className="font-medium">Library</h2>
        {library && <span className="tabular text-sm text-muted-foreground">{library.count}</span>}
        <Button variant="ghost" size="icon-sm" className="ml-auto" aria-label="Refresh library" onClick={() => void load()}>
          <RefreshCwIcon className={loading ? "animate-spin" : ""} />
        </Button>
      </div>
      {songs.length === 0 ? (
        <p className="px-4 py-6 text-center text-sm text-muted-foreground">
          {loading ? "Loading…" : "Downloaded songs show up here, ready to play or save."}
        </p>
      ) : (
        <ul className="max-h-80 divide-y overflow-y-auto">
          {songs.map((song) => (
            <li key={song.path} className="flex items-center gap-3 px-4 py-2">
              <div className="grid size-8 shrink-0 place-items-center rounded-md bg-muted">
                <MusicIcon className="size-4 text-muted-foreground" />
              </div>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm">{song.name}</p>
                <p className="truncate text-xs text-muted-foreground">
                  {[song.folder, bytes(song.size)].filter(Boolean).join(" · ")}
                </p>
              </div>
              <PlayButton song={{ path: song.path, title: song.name }} />
              <SaveButton path={song.path} />
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
