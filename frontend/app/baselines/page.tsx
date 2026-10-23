"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { Plus, RotateCcw } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { get, post, type Baseline } from "@/lib/api";

export default function BaselinesPage() {
  const [items, setItems] = useState<Baseline[] | null>(null);
  const load = () => get<Baseline[]>("/baselines").then(setItems).catch((e) => toast.error(e.message));
  useEffect(() => { load(); }, []);
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Baselines</h1>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" onClick={async () => {
            if (!confirm("Reload the seed baselines? Your edits to them are replaced.")) return;
            await post("/baselines/reset-seed"); toast.success("Seed baselines reloaded"); load();
          }}><RotateCcw /> Reset seed baselines</Button>
          <Link href="/baselines/new" className={buttonVariants()}><Plus /> New baseline</Link>
        </div>
      </div>
      <Card className="py-0">
        <Table>
          <TableHeader><TableRow>
            <TableHead>Title</TableHead><TableHead>Level</TableHead><TableHead className="text-right">Years</TableHead>
            <TableHead className="text-right">Required</TableHead><TableHead className="text-right">Preferred</TableHead><TableHead />
          </TableRow></TableHeader>
          <TableBody>
            {(items ?? []).map((b) => (
              <TableRow key={b.id}>
                <TableCell className="font-medium">
                  <Link href={`/baselines/${b.id}`} className="hover:underline">{b.title}</Link>
                  {b.is_mock && <Badge variant="secondary" className="ml-2">mock</Badge>}
                </TableCell>
                <TableCell>{b.level}</TableCell>
                <TableCell className="text-right tabular-nums">{b.required_years}</TableCell>
                <TableCell className="text-right tabular-nums">{b.criteria.filter((c) => c.priority === "required").length}</TableCell>
                <TableCell className="text-right tabular-nums">{b.criteria.filter((c) => c.priority === "preferred").length}</TableCell>
                <TableCell className="text-right"><Link href={`/baselines/${b.id}`} className={buttonVariants({ variant: "ghost", size: "sm" })}>Edit</Link></TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Card>
    </div>
  );
}
