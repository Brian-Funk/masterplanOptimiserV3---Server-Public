import fs from "node:fs";
import path from "node:path";
import Link from "next/link";

export const dynamic = "force-static";

/** Public security-contact policy, excluding internal engineering reports. */
export default function SecurityPage() {
  const securityPolicy = fs.readFileSync(
    path.join(process.cwd(), "legal-artifacts", "SECURITY.md"),
    "utf8",
  );
  return (
    <main className="min-h-screen bg-gray-50 px-6 py-12 dark:bg-gray-900">
      <article className="mx-auto max-w-4xl space-y-5 text-gray-700 dark:text-gray-300">
        <h1 className="text-3xl font-bold text-gray-900 dark:text-gray-100">Security</h1>
        <p>This page contains the public reporting and support policy shipped with this version. It does not expose private engineering notes, incident evidence, secrets or controller data.</p>
        <pre className="overflow-x-auto whitespace-pre-wrap rounded border bg-white p-5 text-xs dark:border-gray-700 dark:bg-gray-950">{securityPolicy}</pre>
        <Link className="text-blue-600 underline dark:text-blue-400" href="/privacy">Open the instance legal centre</Link>
      </article>
    </main>
  );
}
