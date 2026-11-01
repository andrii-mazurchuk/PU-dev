import type { ReactNode } from "react"
import { AlertDialog as D } from "radix-ui"
import { Button } from "@/components/ui/button"

// An explicit confirm for the few writes that need one (Andrey's rulings): widening a session type's access, pausing
// everything. It names exactly what changes; Esc or Cancel keeps things as they were.
function ConfirmDialog({ open, title, body, confirmLabel, onConfirm, onCancel }: {
  open: boolean; title: string; body: ReactNode; confirmLabel: string; onConfirm: () => void; onCancel: () => void
}) {
  return (
    <D.Root open={open} onOpenChange={o => { if (!o) onCancel() }}>
      <D.Portal>
        <D.Overlay className="fixed inset-0 z-50 bg-background/60 duration-(--duration-overlay) ease-(--ease-out) data-[state=closed]:duration-(--duration-fast) data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=closed]:animate-out data-[state=closed]:fade-out-0" />
        <D.Content className="fixed left-1/2 top-1/2 z-50 flex w-[calc(100%-2rem)] max-w-md -translate-x-1/2 -translate-y-1/2 flex-col gap-4 rounded-lg border border-border bg-card p-6 text-card-foreground shadow-sm duration-(--duration-overlay) ease-(--ease-out) data-[state=closed]:duration-(--duration-fast) data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 data-[state=closed]:animate-out data-[state=closed]:fade-out-0">
          <D.Title className="m-0 text-base font-semibold text-foreground">{title}</D.Title>
          <D.Description className="m-0 text-sm text-muted-foreground">{body}</D.Description>
          <div className="flex justify-end gap-2">
            <D.Cancel asChild><Button variant="outline" size="sm">Cancel</Button></D.Cancel>
            <D.Action asChild><Button size="sm" onClick={onConfirm}>{confirmLabel}</Button></D.Action>
          </div>
        </D.Content>
      </D.Portal>
    </D.Root>
  )
}

export { ConfirmDialog }
