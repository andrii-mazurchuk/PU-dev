import * as React from "react"
import { cn } from "@/lib/utils"

/** suggestions: values offered while typing (a native datalist); any other value can still be typed. */
function Input({ className, type, suggestions, ...props }: React.ComponentProps<"input"> & { suggestions?: string[] }) {
  const listId = React.useId()
  return (
    <>
      <input
        type={type}
        data-slot="input"
        list={suggestions?.length ? listId : undefined}
        className={cn(
          "h-9 w-full min-w-0 rounded-md border border-input bg-transparent px-3 py-1 text-sm transition-[color,box-shadow] duration-(--duration-fast) ease-(--ease-standard) outline-none selection:bg-primary selection:text-primary-foreground file:inline-flex file:h-7 file:border-0 file:bg-transparent file:text-sm file:font-medium file:text-foreground placeholder:text-muted-foreground disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50",
          "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
          "aria-invalid:border-destructive",
          className
        )}
        {...props}
      />
      {!!suggestions?.length && <datalist id={listId}>{suggestions.map(s => <option key={s} value={s} />)}</datalist>}
    </>
  )
}

export { Input }
