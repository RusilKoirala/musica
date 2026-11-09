"use client";

import { useEffect, useState } from "react";
import { Loader2Icon, TriangleAlertIcon } from "lucide-react";
import { toast } from "sonner";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
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
import { Label } from "@/components/ui/label";
import { post, type Drive } from "@/lib/api";
import { SimpleSelect } from "./simple-select";

export function FormatDialog({
  drive,
  onOpenChange,
  onDone,
}: {
  drive: Drive | null;
  onOpenChange: (open: boolean) => void;
  onDone: () => void;
}) {
  return (
    <Dialog open={Boolean(drive)} onOpenChange={onOpenChange}>
      {drive && <FormatForm key={drive.path} drive={drive} onClose={() => onOpenChange(false)} onDone={onDone} />}
    </Dialog>
  );
}

function FormatForm({ drive, onClose, onDone }: { drive: Drive; onClose: () => void; onDone: () => void }) {
  const [fstype, setFstype] = useState("fat32");
  const [label, setLabel] = useState((drive.label || "MUSIC").toUpperCase().slice(0, 11));
  const [confirm, setConfirm] = useState("");
  const [plan, setPlan] = useState<{ command: string; warning: string } | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let stale = false;
    post<{ command: string; warning: string }>("/api/drives/plan", { path: drive.path, fstype, label })
      .then((result) => !stale && setPlan(result))
      .catch((exc) => !stale && setPlan({ command: "", warning: (exc as Error).message }));
    return () => {
      stale = true;
    };
  }, [drive, fstype, label]);

  async function run() {
    setBusy(true);
    try {
      await post("/api/drives/format", { path: drive.path, fstype, label, confirm });
      toast.success(`${label} is formatted and ready`);
      onDone();
      onClose();
    } catch (exc) {
      toast.error((exc as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Format for the car</DialogTitle>
          <DialogDescription>
            Erases everything on <span className="font-medium text-foreground">{drive.label || drive.path}</span> (
            {drive.total_human}, now {drive.fstype}).
          </DialogDescription>
        </DialogHeader>

        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1.5">
            <Label>File system</Label>
            <SimpleSelect
              value={fstype}
              onChange={setFstype}
              options={[
                { value: "fat32", label: "FAT32 (safest)" },
                { value: "exfat", label: "exFAT" },
              ]}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="fmt-label">Name</Label>
            <Input
              id="fmt-label"
              value={label}
              maxLength={11}
              onChange={(e) => setLabel(e.target.value.toUpperCase().replace(/[^A-Z0-9_ -]/g, ""))}
            />
          </div>
        </div>

        {plan && (
          <Alert variant={plan.command ? "default" : "destructive"}>
            <TriangleAlertIcon />
            <AlertTitle>{plan.warning || "This cannot be undone"}</AlertTitle>
            {plan.command && (
              <AlertDescription>
                <code className="font-mono text-xs break-all">{plan.command}</code>
              </AlertDescription>
            )}
          </Alert>
        )}

        <div className="space-y-1.5">
          <Label htmlFor="fmt-confirm">
            Type <span className="font-mono font-semibold">FORMAT</span> to confirm
          </Label>
          <Input id="fmt-confirm" value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="off" />
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="destructive" disabled={confirm !== "FORMAT" || busy || !plan?.command} onClick={run}>
            {busy && <Loader2Icon className="animate-spin" />}
            Erase and format
          </Button>
        </DialogFooter>
      </DialogContent>
  );
}
