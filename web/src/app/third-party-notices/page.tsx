import fs from "node:fs";
import path from "node:path";
import Link from "next/link";

export const dynamic = "force-static";

/** Read-only third-party notices shipped with this source tree. */
export default function ThirdPartyNoticesPage() {
  const notices = fs.readFileSync(
    path.join(process.cwd(), "legal-artifacts", "THIRD-PARTY-NOTICES.md"),
    "utf8",
  );
  return (
    <main className="min-h-screen bg-gray-50 px-6 py-12 dark:bg-gray-900">
      <article className="mx-auto max-w-4xl space-y-5 text-gray-700 dark:text-gray-300">
        <h1 className="text-3xl font-bold text-gray-900 dark:text-gray-100">Third-party notices</h1>
        <p>This read-only inventory is tied to the installed source version. Phase 6 release validation verifies that it is complete and current.</p>
        <pre className="overflow-x-auto whitespace-pre-wrap rounded border bg-white p-5 text-xs dark:border-gray-700 dark:bg-gray-950">{notices}</pre>
        <Link className="text-blue-600 underline dark:text-blue-400" href="/licence">Open the software licence</Link>
      </article>
    </main>
  );
}
