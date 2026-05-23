import { type ReactNode } from 'react'
import Sidebar from './Sidebar'

export default function Shell({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen bg-background text-foreground flex">
      <Sidebar />
      <div className="flex-1 flex flex-col min-w-0">
        <main className="flex-1 overflow-auto px-6 pt-4 pb-6">{children}</main>
      </div>
    </div>
  )
}
