"use client";

export default function GlobalError({
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="en">
      <body className="bg-slate-950 text-slate-200 flex items-center justify-center min-h-screen font-sans">
        <div className="text-center max-w-md px-6">
          <h1 className="text-2xl font-bold text-red-400 mb-4">
            Something went wrong
          </h1>
          <p className="text-slate-400 mb-6">
            An unexpected error occurred. Please try refreshing the page.
          </p>
          <button
            onClick={reset}
            className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg transition-colors"
          >
            Try again
          </button>
        </div>
      </body>
    </html>
  );
}
