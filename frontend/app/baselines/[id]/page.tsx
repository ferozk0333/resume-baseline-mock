"use client";
import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { BaselineForm, EMPTY_BASELINE } from "@/components/features/baselines/baseline-form";
import { del, get, post, put, type Baseline } from "@/lib/api";

export default function BaselineEdit() {
  return <Suspense fallback={<Skeleton className="h-96" />}><BaselineEditInner /></Suspense>;
}

function BaselineEditInner() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const isNew = id === "new";
  const [b, setB] = useState<Baseline | null>(isNew ? EMPTY_BASELINE : null);
  const [confirm, setConfirm] = useState(false);
  useEffect(() => { if (!isNew) get<Baseline>(`/baselines/${id}`).then(setB).catch((e) => toast.error(e.message)); }, [id, isNew]);
  if (!b) return <Skeleton className="h-96" />;
  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between gap-4">
        <div>
          <Link href="/baselines" className="mb-1 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
            <ArrowLeft className="size-4" /> Baselines
          </Link>
          <h1 className="text-2xl font-semibold tracking-tight">{isNew ? "New baseline" : b.title}</h1>
        </div>
        {!isNew && <Button variant="outline" onClick={() => setConfirm(true)}><Trash2 /> Delete</Button>}
      </div>
      <BaselineForm initial={b} submitLabel={isNew ? "Create baseline" : "Save changes"} onSubmit={async (next) => {
        try {
          if (isNew) { const created = await post<Baseline>("/baselines", next); router.push(`/baselines/${created.id}`); }
          else setB(await put<Baseline>(`/baselines/${id}`, next));
          toast.success("Saved");
        } catch (e) { toast.error((e as Error).message); }
      }} />
      <Dialog open={confirm} onOpenChange={setConfirm}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete this baseline?</DialogTitle>
            <DialogDescription>Past scores are kept.</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirm(false)}>Cancel</Button>
            <Button variant="destructive" onClick={async () => { await del(`/baselines/${id}`); router.push("/baselines"); }}>Delete</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
