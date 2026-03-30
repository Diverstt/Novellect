import { MobileBottomNav } from "@/components/navigation/mobile-bottom-nav";
import { TopNav } from "@/components/navigation/top-nav";

export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen pb-20 md:pb-0">
      <TopNav />
      <main className="pt-20">{children}</main>
      <MobileBottomNav />
    </div>
  );
}
