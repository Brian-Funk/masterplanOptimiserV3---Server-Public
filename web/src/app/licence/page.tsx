import fs from "node:fs";
import path from "node:path";
import Link from "next/link";

export const dynamic = "force-static";

/** Read-only copy of the exact software licence shipped in this source tree. */
export default function LicencePage() {
  const licence = fs.readFileSync(
    path.join(process.cwd(), "legal-artifacts", "LICENSE"),
    "utf8",
  );
  return (
    <main className="min-h-screen bg-gray-50 px-6 py-12 dark:bg-gray-900">
      <article className="mx-auto max-w-4xl space-y-5 text-gray-700 dark:text-gray-300">
        <h1 className="text-3xl font-bold text-gray-900 dark:text-gray-100">Software licence</h1>
        <p>This is the exact read-only licence text shipped with this version of Masterplan Optimiser Server. It grants software permissions and is separate from the controller&apos;s instance-specific notices.</p>
        <p className="break-all">The corresponding source for this exact build is available at <a className="text-blue-600 underline dark:text-blue-400" href={process.env.NEXT_PUBLIC_SOURCE_URL} rel="noreferrer" target="_blank">{process.env.NEXT_PUBLIC_SOURCE_REPOSITORY_URL}@{process.env.NEXT_PUBLIC_SOURCE_REVISION}</a>. Modified deployments must replace this build identity with the repository and exact commit that produced their deployed version.</p>
        <pre className="overflow-x-auto whitespace-pre-wrap rounded border bg-white p-5 text-xs dark:border-gray-700 dark:bg-gray-950">{licence}</pre>
        <Link className="text-blue-600 underline dark:text-blue-400" href="/privacy">Open the instance legal centre</Link>
      </article>
    </main>
  );
}
