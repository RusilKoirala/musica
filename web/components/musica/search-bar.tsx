"use client";

import { useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { ArrowRightIcon, Loader2Icon, SearchIcon, XIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Kbd } from "./kbd";

const SPOTIFY = /open\.spotify\.com\/(?:intl-[a-z]+\/)?(track|album|playlist)\//i;
const YOUTUBE = /(youtube\.com|youtu\.be|music\.youtube\.com)/i;

const EXAMPLES = ["arijit singh tum hi ho", "the weeknd blinding lights", "lofi hip hop mix"];

function sourceLabel(text: string) {
  const spotify = text.match(SPOTIFY);
  if (spotify) return `Spotify ${spotify[1].toLowerCase()}`;
  if (YOUTUBE.test(text)) return /[?&]list=/.test(text) ? "YouTube playlist" : "YouTube video";
  if (/^https?:\/\//i.test(text)) return "Link";
  return null;
}

export function SearchBar({ busy, onSearch }: { busy: boolean; onSearch: (query: string) => void }) {
  const [query, setQuery] = useState("");
  const input = useRef<HTMLInputElement>(null);
  const spotifyMode = usePathname()?.startsWith("/spotify");
  const source = sourceLabel(query.trim());

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement;
      if (event.key === "/" && !["INPUT", "TEXTAREA"].includes(target.tagName)) {
        event.preventDefault();
        input.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  function submit(event?: React.FormEvent) {
    event?.preventDefault();
    const value = query.trim();
    if (value && !busy) onSearch(value);
  }

  return (
    <div className="space-y-3">
      <form
        onSubmit={submit}
        className="group flex h-14 items-center gap-3 rounded-xl border bg-card pr-2 pl-4 shadow-xs transition-colors focus-within:border-ring focus-within:ring-3 focus-within:ring-ring/30"
      >
        <SearchIcon className="size-5 shrink-0 text-muted-foreground" />
        <input
          ref={input}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={
            spotifyMode ? "Paste a Spotify playlist, album or song link" : "Song name, YouTube link or Spotify playlist"
          }
          className="h-full min-w-0 flex-1 bg-transparent text-base outline-none placeholder:text-muted-foreground"
          autoFocus
          spellCheck={false}
          autoComplete="off"
        />
        {source && (
          <span className="hidden shrink-0 rounded-md bg-muted px-2 py-1 text-xs font-medium text-muted-foreground sm:block">
            {source}
          </span>
        )}
        {query && !busy && (
          <Button type="button" variant="ghost" size="icon-sm" aria-label="Clear" onClick={() => setQuery("")}>
            <XIcon />
          </Button>
        )}
        {!query && <Kbd className="hidden sm:inline-flex">/</Kbd>}
        <Button type="submit" size="lg" className="h-10 px-4" disabled={busy || !query.trim()}>
          {busy ? <Loader2Icon className="animate-spin" /> : <ArrowRightIcon />}
          <span className="hidden sm:inline">{busy ? "Looking" : "Find"}</span>
        </Button>
      </form>
      <div className="flex flex-wrap items-center gap-1.5 text-sm text-muted-foreground">
        <span className="mr-1">{spotifyMode ? "Playlists have to be public." : "Try"}</span>
        {(spotifyMode ? [] : EXAMPLES).map((example) => (
          <button
            key={example}
            type="button"
            onClick={() => {
              setQuery(example);
              onSearch(example);
            }}
            disabled={busy}
            className="rounded-md border border-dashed px-2 py-0.5 transition-colors hover:border-solid hover:bg-muted hover:text-foreground disabled:opacity-50"
          >
            {example}
          </button>
        ))}
      </div>
    </div>
  );
}
