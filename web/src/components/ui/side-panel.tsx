import type { ReactNode } from "react"
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet"

// The one right-hand panel standard (Andrey, stage 3): task, map, session type and trigger all open here, over the
// view they were clicked in. The way back is always its first line; Esc and the close button do the same.
function SidePanel({ open, onOpenChange, backLabel, backHref, title, description, children }: {
  open: boolean; onOpenChange: (open: boolean) => void; backLabel: string; backHref: string; title: ReactNode; description?: string; children: ReactNode
}) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full gap-0 overflow-y-auto p-0 sm:max-w-[480px]" {...(description ? {} : { "aria-describedby": undefined })}>
        <div className="flex flex-col gap-4 p-4">
          <a data-block="back" href={backHref} className="self-start rounded-sm text-sm text-foreground underline-offset-4 outline-none hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring">
            ← {backLabel}
          </a>
          <span className="-mt-3 text-xs text-muted-foreground">Esc closes</span>
          <SheetTitle className="text-lg font-semibold text-foreground">{title}</SheetTitle>
          {description && <SheetDescription className="text-sm text-muted-foreground">{description}</SheetDescription>}
          {children}
        </div>
      </SheetContent>
    </Sheet>
  )
}

export { SidePanel }
