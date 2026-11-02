import * as React from "react"
import { cn } from "@/lib/utils"

// A native <select> dressed like Input: keyboard, screen readers and phones get the platform's own picker.
function Select({ className, children, ...props }: React.ComponentProps<"select">) {
  return (
    <select
      data-slot="select"
      className={cn(
        "h-9 w-full min-w-0 rounded-md border border-input bg-transparent px-2 text-sm text-foreground transition-[color,box-shadow] duration-(--duration-fast) ease-(--ease-standard) outline-none disabled:cursor-not-allowed disabled:opacity-50",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
        "aria-invalid:border-destructive [&>option]:bg-background",
        className
      )}
      {...props}
    >
      {children}
    </select>
  )
}

export { Select }
