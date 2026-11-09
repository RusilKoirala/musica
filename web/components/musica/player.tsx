"use client";

import { createContext, useContext, useEffect, useRef, useState } from "react";
import { DownloadIcon, PauseIcon, PlayIcon, XIcon } from "lucide-react";
import { Button, buttonVariants } from "@/components/ui/button";
import { fileUrl } from "@/lib/api";
import { cn } from "@/lib/utils";

export type Playing = { path: string; title: string };

const PlayerContext = createContext<{ playing: Playing | null; play: (song: Playing) => void }>({
  playing: null,
  play: () => {},
});

export const usePlayer = () => useContext(PlayerContext);

export function PlayerProvider({ children }: { children: React.ReactNode }) {
  const [playing, setPlaying] = useState<Playing | null>(null);
  return (
    <PlayerContext.Provider value={{ playing, play: setPlaying }}>
      {children}
      {playing && <PlayerBar song={playing} onClose={() => setPlaying(null)} />}
    </PlayerContext.Provider>
  );
}

export function PlayButton({ song, className }: { song: Playing; className?: string }) {
  const { playing, play } = usePlayer();
  const active = playing?.path === song.path;
  return (
    <Button
      variant="ghost"
      size="icon-sm"
      className={cn("relative", active && "text-brand", className)}
      aria-label={`Play ${song.title}`}
      onClick={() => play(song)}
    >
      <PlayIcon />
    </Button>
  );
}

export function SaveButton({ path, className }: { path: string; className?: string }) {
  return (
    <a
      href={fileUrl(path)}
      download
      aria-label="Save to this device"
      title="Save to this device"
      className={cn(buttonVariants({ variant: "ghost", size: "icon-sm" }), "relative", className)}
    >
      <DownloadIcon />
    </a>
  );
}

function PlayerBar({ song, onClose }: { song: Playing; onClose: () => void }) {
  const audio = useRef<HTMLAudioElement>(null);
  const [paused, setPaused] = useState(false);
  const [time, setTime] = useState({ now: 0, total: 0 });

  useEffect(() => {
    audio.current?.play().catch(() => setPaused(true));
  }, [song.path]);

  const format = (s: number) => (Number.isFinite(s) ? `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}` : "0:00");

  return (
    <div className="fixed inset-x-0 bottom-0 z-40 border-t bg-background/90 backdrop-blur">
      <audio
        ref={audio}
        src={fileUrl(song.path)}
        onPlay={() => setPaused(false)}
        onPause={() => setPaused(true)}
        onTimeUpdate={(e) => setTime({ now: e.currentTarget.currentTime, total: e.currentTarget.duration })}
        onEnded={() => setPaused(true)}
      />
      <div className="mx-auto flex h-16 max-w-6xl items-center gap-3 px-4">
        <Button
          size="icon"
          className="shrink-0 rounded-full bg-brand text-brand-foreground hover:bg-brand/90"
          aria-label={paused ? "Play" : "Pause"}
          onClick={() => (paused ? audio.current?.play() : audio.current?.pause())}
        >
          {paused ? <PlayIcon /> : <PauseIcon />}
        </Button>
        <div className="min-w-0 flex-1 space-y-1.5">
          <p className="truncate text-sm font-medium">{song.title}</p>
          <div className="flex items-center gap-2">
            <input
              type="range"
              min={0}
              max={time.total || 0}
              step={0.1}
              value={time.now}
              onChange={(e) => audio.current && (audio.current.currentTime = Number(e.target.value))}
              className="h-1 flex-1 cursor-pointer accent-(--brand)"
              aria-label="Seek"
            />
            <span className="tabular w-20 text-right text-xs text-muted-foreground">
              {format(time.now)} / {format(time.total)}
            </span>
          </div>
        </div>
        <SaveButton path={song.path} />
        <Button variant="ghost" size="icon-sm" aria-label="Close player" onClick={onClose}>
          <XIcon />
        </Button>
      </div>
    </div>
  );
}
