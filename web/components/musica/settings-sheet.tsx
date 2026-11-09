"use client";

import { useState } from "react";
import { CheckCircle2Icon, Loader2Icon, LogOutIcon, MonitorIcon, MoonIcon, RefreshCwIcon, SunIcon, XCircleIcon } from "lucide-react";
import { useTheme } from "next-themes";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Switch } from "@/components/ui/switch";
import { post, type Health, type Settings } from "@/lib/api";
import { cn } from "@/lib/utils";
import { SimpleSelect } from "./simple-select";

function Row({ title, hint, children, htmlFor }: { title: string; hint?: string; children: React.ReactNode; htmlFor?: string }) {
  return (
    <div className="flex items-center justify-between gap-4 py-2.5">
      <div className="min-w-0 space-y-0.5">
        <Label htmlFor={htmlFor} className="text-sm font-medium">
          {title}
        </Label>
        {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h3 className="mb-1 text-xs font-medium text-muted-foreground">{title}</h3>
      <div className="divide-y">{children}</div>
    </section>
  );
}

function Check({ ok, label, value }: { ok: boolean; label: string; value: string }) {
  return (
    <div className="flex items-center gap-2 py-1.5 text-sm">
      {ok ? <CheckCircle2Icon className="size-4 text-success" /> : <XCircleIcon className="size-4 text-destructive" />}
      <span>{label}</span>
      <span className="ml-auto truncate font-mono text-xs text-muted-foreground">{value}</span>
    </div>
  );
}

export function SettingsSheet({
  open,
  onOpenChange,
  settings,
  onSettings,
  health,
  onHealth,
  onSignOut,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  settings: Settings | null;
  onSettings: (partial: Partial<Settings>) => void;
  health: Health | null;
  onHealth: () => void;
  onSignOut: () => void;
}) {
  const { theme, setTheme } = useTheme();
  const [updating, setUpdating] = useState(false);
  const [cookies, setCookies] = useState<string | null>(null);

  async function updateYtdlp() {
    setUpdating(true);
    try {
      const result = await post<{ ok: boolean; before: string; after: string; error?: string }>("/api/selfupdate");
      if (result.ok) {
        toast.success(result.before === result.after ? `yt-dlp ${result.after} is already the newest` : `yt-dlp updated to ${result.after}`);
      } else toast.error(result.error || "Update failed");
      onHealth();
    } catch (exc) {
      toast.error((exc as Error).message);
    } finally {
      setUpdating(false);
    }
  }

  const toggle = (key: keyof Settings, title: string, hint?: string) => (
    <Row title={title} hint={hint} htmlFor={key}>
      <Switch
        id={key}
        checked={Boolean(settings?.[key])}
        onCheckedChange={(checked) => onSettings({ [key]: checked } as Partial<Settings>)}
      />
    </Row>
  );

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-full gap-0 sm:max-w-md">
        <SheetHeader className="border-b">
          <SheetTitle>Settings</SheetTitle>
          <SheetDescription>Saved on the server and used for every download.</SheetDescription>
        </SheetHeader>

        {settings && (
          <div className="flex-1 space-y-6 overflow-y-auto p-4">
            <Section title="Files">
              <Row title="Naming">
                <SimpleSelect
                  className="w-40"
                  value={settings.naming}
                  onChange={(naming) => onSettings({ naming: naming as Settings["naming"] })}
                  options={[
                    { value: "artist_title", label: "Artist - Title" },
                    { value: "title", label: "Title only" },
                  ]}
                />
              </Row>
              {toggle("number_files", "Number playlist tracks", "01 - Song.mp3, so they play in order")}
              {toggle("playlist_subfolders", "Folder per playlist")}
              {toggle("skip_name_duplicates", "Skip near-duplicates", "Songs whose name matches a file already there")}
            </Section>

            <Section title="Car stereo">
              {toggle("car_mode", "Car mode", "ID3v2.3 tags, small cover art, folder.jpg")}
              <Row title="Genre tag" htmlFor="car_genre">
                <Input
                  id="car_genre"
                  className="w-40"
                  defaultValue={settings.car_genre}
                  onBlur={(e) => e.target.value !== settings.car_genre && onSettings({ car_genre: e.target.value })}
                />
              </Row>
            </Section>

            <Section title="Downloading">
              <Row title="Search results">
                <SimpleSelect
                  className="w-40"
                  value={String(settings.search_results)}
                  onChange={(v) => onSettings({ search_results: Number(v) })}
                  options={["5", "10", "20", "30", "50"].map((v) => ({ value: v, label: `${v} results` }))}
                />
              </Row>
              <Row title="At the same time" hint="1 is gentlest on slow drives">
                <SimpleSelect
                  className="w-40"
                  value={String(settings.concurrency)}
                  onChange={(v) => onSettings({ concurrency: Number(v) })}
                  options={["1", "2", "3", "4"].map((v) => ({ value: v, label: `${v} at a time` }))}
                />
              </Row>
              {toggle("stage_locally", "Stage on internal disk", "Convert first, then copy one finished file")}
              {toggle("archive_on_drive", "Archive on the drive", "Keep download history with the files")}
              <div className="space-y-1.5 py-2.5">
                <Label htmlFor="cookiefile" className="text-sm font-medium">
                  Cookies file
                </Label>
                <p className="text-xs text-muted-foreground">Path to a cookies.txt for videos that need sign-in.</p>
                <Input
                  id="cookiefile"
                  placeholder="/data/cookies.txt"
                  className="font-mono text-xs"
                  value={cookies ?? settings.cookiefile}
                  onChange={(e) => setCookies(e.target.value)}
                  onBlur={() => {
                    if (cookies !== null && cookies !== settings.cookiefile) onSettings({ cookiefile: cookies });
                    setCookies(null);
                  }}
                />
              </div>
            </Section>

            <Section title="Appearance">
              <div className="grid grid-cols-3 gap-1.5 py-2.5">
                {[
                  { value: "light", label: "Light", icon: SunIcon },
                  { value: "dark", label: "Dark", icon: MoonIcon },
                  { value: "system", label: "System", icon: MonitorIcon },
                ].map(({ value, label, icon: Icon }) => (
                  <Button
                    key={value}
                    variant="outline"
                    className={cn(theme === value && "border-ring bg-muted")}
                    onClick={() => setTheme(value)}
                  >
                    <Icon /> {label}
                  </Button>
                ))}
              </div>
            </Section>

            <Section title="Server">
              <div className="py-2">
                {health && (
                  <>
                    <Check ok label="yt-dlp" value={health.ytdlp} />
                    <Check ok={health.ffmpeg.ok} label="ffmpeg" value={health.ffmpeg.version ?? "missing"} />
                    <Check ok={Boolean(health.js_runtime)} label="JS runtime" value={health.js_runtime ?? "missing"} />
                    <Check ok label="Musica" value={`${health.version} · ${health.os}`} />
                  </>
                )}
                <Button variant="outline" className="mt-3 w-full" onClick={updateYtdlp} disabled={updating}>
                  {updating ? <Loader2Icon className="animate-spin" /> : <RefreshCwIcon />}
                  {updating ? "Updating yt-dlp" : "Update yt-dlp"}
                </Button>
                <p className="mt-2 text-xs text-muted-foreground">When YouTube downloads start failing, this usually fixes it.</p>
              </div>
            </Section>

            {health?.auth && (
              <>
                <Separator />
                <Button variant="ghost" className="w-full text-muted-foreground" onClick={onSignOut}>
                  <LogOutIcon /> Sign out
                </Button>
              </>
            )}
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}
