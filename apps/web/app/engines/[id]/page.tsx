'use client';

import Link from 'next/link';
import { use } from 'react';

import { EngineDetail } from '@/features/engine/engine-detail';
import { useFleetStream, useTwinStream } from '@/hooks/use-fleet-stream';

export default function EnginePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);

  // The fleet stream supplies the summary row, which lets the header render
  // immediately rather than waiting for the first per-engine delta.
  useFleetStream();
  useTwinStream(id);

  return (
    <main className="mx-auto max-w-[1500px] px-6 py-6">
      <Link
        href="/fleet"
        className="mb-4 inline-block text-xs text-tertiary transition-colors hover:text-secondary"
      >
        ← Back to fleet
      </Link>
      <EngineDetail engineId={id} />
    </main>
  );
}
