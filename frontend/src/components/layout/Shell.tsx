import { type ReactNode } from 'react'
import Sidebar from './Sidebar'

export default function Shell({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen bg-background text-foreground flex">
      <Sidebar />
      <div className="flex-1 flex flex-col min-w-0">
        <header className="h-14 border-b flex items-center px-6 text-base font-semibold">
          smart-search
        </header>
        <main className="flex-1 overflow-auto p-6">{children}</main>
      </div>
    </div>
  )
}
