"use client";

import { useState } from "react";
import { Loader2Icon, PlayIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { post } from "@/lib/api";
import { Logo } from "./logo";

export function Login({ onDone, demo }: { onDone: () => void; demo?: boolean }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<"password" | "demo" | null>(null);

  async function signIn(body: { password?: string; demo?: boolean }) {
    setBusy(body.demo ? "demo" : "password");
    setError("");
    try {
      await post("/api/login", body);
      onDone();
    } catch (exc) {
      setError((exc as Error).message);
    } finally {
      setBusy(null);
    }
  }

  function submit(event: React.FormEvent) {
    event.preventDefault();
    void signIn({ password });
  }

  return (
    <main className="grid min-h-svh place-items-center px-4">
      <div className="w-full max-w-sm space-y-6">
        <Logo className="justify-center" />
        <Card>
          <CardHeader>
            <CardTitle>Sign in</CardTitle>
            <CardDescription>This Musica server is password protected.</CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={submit} className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="password">Password</Label>
                <Input
                  id="password"
                  type="password"
                  autoFocus
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  aria-invalid={Boolean(error)}
                />
                {error && <p className="text-sm text-destructive">{error}</p>}
              </div>
              <Button type="submit" className="w-full" size="lg" disabled={Boolean(busy) || !password}>
                {busy === "password" && <Loader2Icon className="animate-spin" />}
                Continue
              </Button>
            </form>
            {demo && (
              <>
                <div className="my-4 flex items-center gap-3 text-xs text-muted-foreground">
                  <span className="h-px flex-1 bg-border" />
                  or
                  <span className="h-px flex-1 bg-border" />
                </div>
                <Button
                  variant="outline"
                  size="lg"
                  className="w-full"
                  disabled={Boolean(busy)}
                  onClick={() => void signIn({ demo: true })}
                >
                  {busy === "demo" ? <Loader2Icon className="animate-spin" /> : <PlayIcon />}
                  Try the demo
                </Button>
                <p className="mt-2 text-center text-xs text-muted-foreground">No password needed. A few songs at a time.</p>
              </>
            )}
          </CardContent>
        </Card>
      </div>
    </main>
  );
}
