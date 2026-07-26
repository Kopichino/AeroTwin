import { TableSkeleton } from '@/components/ui';

export default function Loading() {
  return (
    <div className="mx-auto max-w-[1500px] px-6 py-6">
      <div className="glass-panel rounded-md">
        <TableSkeleton rows={10} />
      </div>
    </div>
  );
}
