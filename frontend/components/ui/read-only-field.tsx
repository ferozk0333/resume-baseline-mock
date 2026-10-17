import * as React from "react"
import { cn } from "@/lib/utils"

/** A saved value shown in view mode (specs/19 item 3): no input chrome, a subtle background, dark text. It takes the
 * same place in the grid as the input that replaces it in edit mode, so nothing moves when a card switches. */
function ReadOnlyField({ label, value, empty = "—", className, children }: {
  label: string; value?: React.ReactNode; empty?: string; className?: string; children?: React.ReactNode
}) {
  const content = children ?? value
  const blank = content === null || content === undefined || content === ""
  return (
    <div className={cn("min-w-0 space-y-1.5", className)} data-slot="read-only-field">
      <div className="text-sm font-medium leading-none">{label}</div>
      <div data-readonly="true"
        className={cn("min-h-8 rounded-lg bg-readonly px-2.5 py-1.5 text-sm break-words text-field-foreground",
          blank && "text-muted-foreground")}>
        {blank ? empty : content}
      </div>
    </div>
  )
}

export { ReadOnlyField }
