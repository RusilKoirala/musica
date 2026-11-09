"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { FolderIcon, Loader2Icon, SettingsIcon, TriangleAlertIcon } from "lucide-react";
import { toast } from "sonner";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { DestinationBar } from "@/components/musica/destination-bar";
import { FolderDialog } from "@/components/musica/folder-dialog";
import { FormatDialog } from "@/components/musica/format-dialog";
import { Login } from "@/components/musica/login";
import { Logo } from "@/components/musica/logo";
import { QueuePanel } from "@/components/musica/queue-panel";
import { isOwned, Results, ResultsSkeleton } from "@/components/musica/results";
import { SearchBar } from "@/components/musica/search-bar";
import { SettingsSheet } from "@/components/musica/settings-sheet";
import { useQueue } from "@/hooks/use-queue";
import {
  api,
  ApiError,
  itemKey,
  post,
  put,
  type Drive,
  type DrivesResponse,
  type Health,
  type ResolveResponse,
  type Settings,
  type SettingsResponse,
} from "@/lib/api";
import { plural } from "@/lib/format";

type Auth = "checking" | "in" | "out" | "offline";

export default function Home() {
  const [auth, setAuth] = useState<Auth>("checking");
  const [health, setHealth] = useState<Health | null>(null);
  const [role, setRole] = useState<"admin" | "demo">("admin");

  const loadHealth = useCallback(async () => {
    try {
      const result = await api<Health>("/api/health");
      setHealth(result);
      return result;
    } catch {
      setAuth("offline");
      return null;
    }
  }, []);

  const checkAuth = useCallback(async () => {
    const result = await loadHealth();
    if (!result) return;
    if (!result.auth) return setAuth("in");
    try {
      const me = await api<{ role: "admin" | "demo" }>("/api/me");
      setRole(me.role);
      setAuth("in");
    } catch (exc) {
      setAuth(exc instanceof ApiError && exc.status === 401 ? "out" : "offline");
    }
  }, [loadHealth]);

  useEffect(() => {
    // Every setState in checkAuth runs after an await, never during this effect.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void checkAuth();
    const onUnauthorized = () => setAuth("out");
    window.addEventListener("musica:unauthorized", onUnauthorized);
    return () => window.removeEventListener("musica:unauthorized", onUnauthorized);
  }, [checkAuth]);

  if (auth === "checking") {
    return (
      <main className="grid min-h-svh place-items-center">
        <Loader2Icon className="size-5 animate-spin text-muted-foreground" />
      </main>
    );
  }
  if (auth === "offline") {
    return (
      <main className="grid min-h-svh place-items-center px-4">
        <div className="max-w-sm space-y-4 text-center">
          <Logo className="justify-center" />
          <p className="text-sm text-muted-foreground">Can&apos;t reach the Musica server. Is it running?</p>
          <Button variant="outline" onClick={() => void checkAuth()}>
            Try again
          </Button>
        </div>
      </main>
    );
  }
  if (auth === "out") return <Login demo={health?.demo} onDone={() => void checkAuth()} />;
  return (
    <App
      health={health}
      demoLimit={role === "demo" ? (health?.demo_limit ?? 5) : null}
      onHealth={loadHealth}
      onSignOut={() => post("/api/logout").then(() => setAuth("out"))}
    />
  );
}

function App({
  health,
  demoLimit,
  onHealth,
  onSignOut,
}: {
  health: Health | null;
  // Set for demo visitors: the most songs one download may hold.
  demoLimit: number | null;
  onHealth: () => void;
  onSignOut: () => void;
}) {
  const demo = demoLimit !== null;
  const { queue, logs, connected } = useQueue(true);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [drives, setDrives] = useState<DrivesResponse | null>(null);
  const [destination, setDestination] = useState("");
  const [preview, setPreview] = useState<ResolveResponse | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [searching, setSearching] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [browseOpen, setBrowseOpen] = useState(false);
  const [formatting, setFormatting] = useState<Drive | null>(null);
  const searchId = useRef(0);

  // Show progress in the tab title, so it can sit in the background.
  const left = queue ? queue.counts.pending + queue.counts.running : 0;
  useEffect(() => {
    document.title = left ? `(${left}) Musica` : "Musica";
  }, [left]);

  // Say so once when a batch finishes, wherever the person is on the page.
  const seen = useRef<Map<string, string>>(new Map());
  useEffect(() => {
    for (const job of queue?.jobs ?? []) {
      const before = seen.current.get(job.id);
      seen.current.set(job.id, job.status);
      if (before && before !== "finished" && job.status === "finished") {
        const { done, error } = job.counts;
        const text = `${job.label || "Download"}: ${plural(done, "song")} saved`;
        if (error) toast.warning(`${text}, ${error} failed`);
        else toast.success(text);
      }
    }
  }, [queue]);

  const loadDrives = useCallback(async () => {
    setRefreshing(true);
    try {
      const result = await api<DrivesResponse>("/api/drives");
      setDrives(result);
      return result;
    } catch (exc) {
      toast.error((exc as Error).message);
      return null;
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    (async () => {
      const [settingsResult, drivesResult] = await Promise.all([
        api<SettingsResponse>("/api/settings").catch(() => null),
        loadDrives(),
      ]);
      if (settingsResult) setSettings(settingsResult.settings);
      setDestination(
        settingsResult?.settings.destination || drivesResult?.drives[0]?.path || drivesResult?.fallback || "",
      );
    })();
  }, [loadDrives]);

  const saveSettings = useCallback(
    async (partial: Partial<Settings>) => {
      const before = settings;
      setSettings((current) => (current ? { ...current, ...partial } : current));
      // Demo visitors only change their own downloads, never the server's settings.
      if (demo) return true;
      try {
        setSettings(await put<Settings>("/api/settings", { settings: partial }));
        return true;
      } catch (exc) {
        setSettings(before);
        toast.error((exc as Error).message);
        return false;
      }
    },
    [settings, demo],
  );

  async function search(query: string) {
    const id = ++searchId.current;
    setSearching(true);
    setPreview(null);
    try {
      const result = await post<ResolveResponse>("/api/resolve", { query });
      if (id !== searchId.current) return;
      setPreview(result);
      const fresh = result.items.filter((item) => !isOwned(item)).map(itemKey);
      setSelected(new Set(demoLimit ? fresh.slice(0, demoLimit) : fresh));
    } catch (exc) {
      if (id === searchId.current) toast.error((exc as Error).message);
    } finally {
      if (id === searchId.current) setSearching(false);
    }
  }

  // A different destination changes which songs are "already there", so re-check them
  // and copy only those flags across, keeping the person's ticks.
  async function changeDestination(path: string) {
    const previous = destination;
    setDestination(path);
    if (!(await saveSettings({ destination: path }))) {
      setDestination(previous);
      return;
    }
    if (!preview) return;
    try {
      const fresh = await post<ResolveResponse>("/api/resolve", { query: preview.url || preview.query });
      const byKey = new Map(fresh.items.map((item) => [itemKey(item), item]));
      setPreview((current) =>
        current && {
          ...current,
          items: current.items.map((item) => {
            const next = byKey.get(itemKey(item));
            return next
              ? { ...item, in_archive: next.in_archive, name_duplicate: next.name_duplicate, duplicate_display: next.duplicate_display }
              : item;
          }),
        },
      );
    } catch {
      // The server applies the same skip rules at download time, so this is cosmetic.
    }
  }

  async function download(force: boolean) {
    if (!preview || !settings) return;
    const items = preview.items.filter((item) => selected.has(itemKey(item)));
    setDownloading(true);
    try {
      const result = await post<{ queued: number; skipped: number }>("/api/download", {
        destination,
        items,
        label: preview.playlist_title || preview.query,
        force,
        settings: { format: settings.format, quality: settings.quality },
      });
      if (result.queued) {
        toast.success(
          `${plural(result.queued, "song")} queued` + (result.skipped ? ` · ${result.skipped} already there` : ""),
        );
      } else {
        toast(`Nothing new: ${plural(result.skipped, "song")} already there`);
      }
    } catch (exc) {
      toast.error((exc as Error).message);
    } finally {
      setDownloading(false);
    }
  }

  const warnings = [
    health && !health.ffmpeg.ok && "ffmpeg is not installed on the server, so audio conversion will fail.",
    health && !health.js_runtime && "No JavaScript runtime (node, deno or bun) on the server. YouTube may refuse lookups.",
  ].filter(Boolean) as string[];

  return (
    <div className="min-h-svh">
      <header className="sticky top-0 z-30 border-b bg-background/80 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-6xl items-center gap-3 px-4">
          <Logo />
          {demo && (
            <span className="rounded-md bg-brand/15 px-2 py-0.5 text-xs font-medium text-brand">Demo</span>
          )}
          <div className="ml-auto flex items-center gap-1">
            {health?.root && (
              <span
                title={health.root}
                className="hidden items-center gap-1.5 rounded-md border px-2 py-1 text-xs text-muted-foreground md:flex"
              >
                <FolderIcon className="size-3.5" />
                {health.root.split(/[\\/]/).filter(Boolean).pop() || health.root}
              </span>
            )}
            <Button variant="ghost" size="icon" aria-label="Settings" onClick={() => setSettingsOpen(true)}>
              <SettingsIcon />
            </Button>
          </div>
        </div>
      </header>

      <main className="mx-auto grid max-w-6xl gap-6 px-4 py-6 lg:grid-cols-[minmax(0,1fr)_380px] lg:py-10">
        <div className="min-w-0 space-y-4">
          <div className="space-y-1 pb-2">
            <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">Any song, straight to your drive.</h1>
            <p className="text-sm text-muted-foreground">
              Search a name or paste a YouTube or Spotify link. Files arrive named, tagged and with cover art.
            </p>
          </div>

          {warnings.map((warning) => (
            <Alert key={warning} variant="destructive">
              <TriangleAlertIcon />
              <AlertTitle>Server setup</AlertTitle>
              <AlertDescription>{warning}</AlertDescription>
            </Alert>
          ))}

          <SearchBar busy={searching} onSearch={search} />
          <DestinationBar
            drives={drives}
            destination={destination}
            onDestination={changeDestination}
            settings={settings}
            onSettings={saveSettings}
            canFormat={Boolean(health?.can_format)}
            onBrowse={() => setBrowseOpen(true)}
            onRefresh={loadDrives}
            onFormatDrive={setFormatting}
            refreshing={refreshing}
          />

          {searching && <ResultsSkeleton />}
          {preview && !searching && (
            <Results
              preview={preview}
              selected={selected}
              onSelectedChange={setSelected}
              onDownload={download}
              downloading={downloading}
              canDownload={Boolean(destination && settings)}
              limit={demoLimit}
            />
          )}
        </div>

        <aside className="lg:sticky lg:top-20 lg:self-start">
          <QueuePanel queue={queue} logs={logs} connected={connected} />
        </aside>
      </main>

      <SettingsSheet
        demo={demo}
        open={settingsOpen}
        onOpenChange={setSettingsOpen}
        settings={settings}
        onSettings={saveSettings}
        health={health}
        onHealth={onHealth}
        onSignOut={onSignOut}
      />
      <FolderDialog open={browseOpen} onOpenChange={setBrowseOpen} start={destination} onPick={changeDestination} />
      <FormatDialog drive={formatting} onOpenChange={(open) => !open && setFormatting(null)} onDone={loadDrives} />
    </div>
  );
}
