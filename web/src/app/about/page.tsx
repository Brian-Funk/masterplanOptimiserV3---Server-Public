import Link from "next/link";

export default function AboutPage() {
  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-900">
      <div className="max-w-3xl mx-auto px-6 py-12">
        <h1 className="text-3xl font-bold text-gray-900 dark:text-gray-100 mb-8">
          About
        </h1>

        <div className="space-y-6 text-gray-700 dark:text-gray-300">
          <section>
            <h2 className="text-xl font-semibold text-gray-900 dark:text-gray-100 mt-6 mb-3">
              Masterplan Optimiser
            </h2>
            <p>
              Masterplan Optimiser is a scheduling and resource-allocation
              platform built for event organisers. It combines constraint-based
              optimisation with an intuitive calendar interface to help teams
              plan complex multi-day events efficiently.
            </p>
          </section>

          <section>
            <h2 className="text-xl font-semibold text-gray-900 dark:text-gray-100 mt-6 mb-3">
              Credits
            </h2>
            <p>
              Designed and developed by{" "}
              <strong className="text-gray-900 dark:text-gray-100">
                Brian Funk
              </strong>
              .
            </p>
          </section>

          <section>
            <h2 className="text-xl font-semibold text-gray-900 dark:text-gray-100 mt-6 mb-3">
              Technology
            </h2>
            <p>Built with the following open-source technologies:</p>
            <ul className="list-disc pl-6 mt-2 space-y-1">
              <li>
                <strong>Next.js</strong> &amp; <strong>React</strong> - user
                interface
              </li>
              <li>
                <strong>FastAPI</strong> - backend API
              </li>
              <li>
                <strong>Google OR-Tools</strong> - constraint-based schedule
                optimisation
              </li>
              <li>
                <strong>SQLAlchemy</strong> - database access
              </li>
              <li>
                <strong>Tailwind CSS</strong> - styling
              </li>
              <li>
                <strong>WebAuthn / Passkeys</strong> - passwordless
                authentication
              </li>
            </ul>
          </section>

          <section>
            <h2 className="text-xl font-semibold text-gray-900 dark:text-gray-100 mt-6 mb-3">
              Version
            </h2>
            <p>
              Web Application&ensp;
              <code className="px-1.5 py-0.5 bg-gray-100 dark:bg-gray-700 rounded text-sm">
                v{process.env.NEXT_PUBLIC_APP_VERSION}
              </code>
            </p>
            <p className="mt-2 break-all text-sm">
              Corresponding source for this exact build: {" "}
              <a
                className="text-blue-600 underline dark:text-blue-400"
                href={process.env.NEXT_PUBLIC_SOURCE_URL}
                rel="noreferrer"
                target="_blank"
              >
                {process.env.NEXT_PUBLIC_SOURCE_REPOSITORY_URL}@
                {process.env.NEXT_PUBLIC_SOURCE_REVISION}
              </a>
            </p>
          </section>
        </div>

        <div className="mt-12 pt-6 border-t border-gray-200 dark:border-gray-700">
          <Link
            href="/login"
            className="text-blue-600 dark:text-blue-400 hover:underline text-sm"
          >
            &larr; Back to Login
          </Link>
        </div>
      </div>
    </div>
  );
}
