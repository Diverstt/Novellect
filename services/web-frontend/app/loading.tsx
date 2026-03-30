import { Skeleton } from "@/components/ui/skeleton";

export default function Loading() {
  return (
    <div className="page-frame py-10">
      <div className="mx-auto flex max-w-2xl flex-col gap-4">
        <Skeleton className="h-8 w-40" />
        <Skeleton className="h-28 w-full" />
        <Skeleton className="h-28 w-full" />
      </div>
    </div>
  );
}
