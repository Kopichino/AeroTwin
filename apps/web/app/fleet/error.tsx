'use client';

export default function Error({ error, reset }: { error: Error; reset: () => void }) {
  return (
    <div className="mx-auto max-w-lg px-6 py-24 text-center">
      <h2 className="text-lg font-semibold">Something went wrong</h2>
      <p className="mt-2 text-sm text-secondary">{error.message}</p>
      <button
        type="button"
        onClick={reset}
        className="mt-6 rounded-md border border-line-strong px-4 py-2 text-sm transition-colors hover:bg-white/5"
      >
        Try again
      </button>
    </div>
  );
}
