"use client"
import * as React from "react"
import { FileText, UploadCloud, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

/** Why a file can't be used, in plain words, or null when it's fine. */
export function fileProblem(file: File, accept: string[], typesLabel: string, maxBytes: number): string | null {
  const ext = (file.name.match(/\.[^.]+$/)?.[0] ?? "").toLowerCase()
  if (!accept.includes(ext)) {
    return `${ext ? `That's a ${ext.slice(1).toUpperCase()} file` : "That file has no type"}. Upload a ${typesLabel} file.`
  }
  if (file.size > maxBytes) return `That file is ${formatBytes(file.size)}. The limit is ${formatBytes(maxBytes)}.`
  return null
}

/** The shared upload drop zone (specs/19 item 2). Replaces the browser's "Choose File / No file chosen".
 * The real file input stays in the zone (visually hidden), so the whole zone is clickable and keyboard focusable. */
export function FileDrop({ id, label, required, accept, typesLabel, what = "file", file, onFile, maxBytes, hint }: {
  id: string; label: string; required?: boolean; accept: string[]; typesLabel: string; what?: string
  file: File | null; onFile: (f: File | null) => void; maxBytes: number; hint?: React.ReactNode
}) {
  const input = React.useRef<HTMLInputElement>(null)
  const [over, setOver] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)
  const errorId = `${id}-error`

  function take(f: File | undefined | null) {
    if (!f) return
    const problem = fileProblem(f, accept, typesLabel, maxBytes)
    setError(problem)
    if (!problem) onFile(f)
    if (input.current) input.current.value = ""        // picking the same file again still fires change
  }

  const picker = (
    <input ref={input} id={id} type="file" accept={accept.join(",")} className="peer sr-only"
      aria-labelledby={`${id}-label`} aria-required={required} aria-invalid={!!error}
      aria-describedby={error ? errorId : undefined}
      onChange={(e) => take(e.target.files?.[0])} />
  )

  return (
    <div className="space-y-1.5" data-testid={`drop-${id}`}>
      <div className="text-sm font-medium leading-none" id={`${id}-label`}>{label}{required && " *"}</div>
      {file ? (
        <div className="flex flex-wrap items-center gap-3 rounded-lg border border-field-border bg-field px-3 py-2.5">
          <FileText className="size-5 shrink-0 text-primary" aria-hidden />
          <div className="min-w-[9rem] flex-1">
            <div className="truncate text-sm font-medium" data-testid={`${id}-name`}>{file.name}</div>
            <div className="text-xs text-muted-foreground">{formatBytes(file.size)}</div>
          </div>
          <label className="cursor-pointer">
            {picker}
            <span className="inline-flex h-7 items-center rounded-md border border-field-border bg-field px-2.5 text-sm font-medium hover:bg-muted peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-ring">
              Replace<span className="sr-only"> {label}</span>
            </span>
          </label>
          <Button type="button" variant="ghost" size="sm" onClick={() => { onFile(null); setError(null) }}>
            <X /> Remove<span className="sr-only"> {label}</span>
          </Button>
        </div>
      ) : (
        <label
          onDragOver={(e) => { e.preventDefault(); setOver(true) }} onDragLeave={() => setOver(false)}
          onDrop={(e) => { e.preventDefault(); setOver(false); take(e.dataTransfer.files?.[0]) }}
          className={cn("relative flex cursor-pointer flex-col items-center justify-center gap-1 rounded-lg border-2 border-dashed border-field-border bg-field px-4 py-6 text-center transition-colors hover:bg-muted/60",
            "has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-ring",
            over && "border-primary bg-primary/5", error && "border-destructive")}>
          {picker}
          <UploadCloud className="size-6 text-muted-foreground" aria-hidden />
          <span className="text-sm">Drop your {what} here or <span className="font-medium text-primary underline underline-offset-2">browse</span></span>
          <span className="text-xs text-muted-foreground">{typesLabel} · up to {formatBytes(maxBytes)}</span>
        </label>
      )}
      {error && <p id={errorId} role="alert" className="text-xs text-destructive">{error}</p>}
      {hint}
    </div>
  )
}
