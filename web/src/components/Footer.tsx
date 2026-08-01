import Link from "next/link";
import { DeleteMyDataLink } from "./DeleteMyDataLink";

/** Footer with policy links, deletion access, and version display. */
export function Footer() {
  return (
    <footer className="border-t border-gray-200 dark:border-gray-700 bg-white/50 dark:bg-gray-800/50 mt-auto">
      <div className="max-w-5xl mx-auto px-4 py-4 flex flex-col sm:flex-row items-center justify-between gap-2 text-xs text-gray-500 dark:text-gray-400">
        <span>
          Masterplan Optimiser self-hosted instance
        </span>
        <nav className="flex items-center gap-3">
          <Link
            href="/about"
            className="hover:text-gray-700 dark:hover:text-gray-200 transition-colors"
          >
            About
          </Link>
          <span className="text-gray-300 dark:text-gray-600">|</span>
          <Link
            href="/privacy"
            className="hover:text-gray-700 dark:hover:text-gray-200 transition-colors"
          >
            Privacy
          </Link>
          <DeleteMyDataLink />
          <span className="text-gray-300 dark:text-gray-600">|</span>
          <Link
            href="/legal"
            className="hover:text-gray-700 dark:hover:text-gray-200 transition-colors"
          >
            Legal
          </Link>
          <span className="text-gray-300 dark:text-gray-600">|</span>
          <Link href="/terms" className="hover:text-gray-700 dark:hover:text-gray-200 transition-colors">Terms</Link>
          <span className="text-gray-300 dark:text-gray-600">|</span>
          <Link href="/licence" className="hover:text-gray-700 dark:hover:text-gray-200 transition-colors">AGPL-3.0-only</Link>
          <span className="text-gray-300 dark:text-gray-600">|</span>
          <Link href="/third-party-notices" className="hover:text-gray-700 dark:hover:text-gray-200 transition-colors">Notices</Link>
          <span className="text-gray-300 dark:text-gray-600">|</span>
          <a
            href={process.env.NEXT_PUBLIC_SOURCE_URL}
            rel="noreferrer"
            target="_blank"
            className="hover:text-gray-700 dark:hover:text-gray-200 transition-colors"
          >
            Source {process.env.NEXT_PUBLIC_SOURCE_REVISION?.slice(0, 12)}
          </a>
          <span className="text-gray-300 dark:text-gray-600">|</span>
          <Link href="/security" className="hover:text-gray-700 dark:hover:text-gray-200 transition-colors">Security</Link>
          <span className="text-gray-300 dark:text-gray-600">|</span>
          <Link
            href="/disclaimer"
            className="hover:text-gray-700 dark:hover:text-gray-200 transition-colors"
          >
            Disclaimer
          </Link>
          <span className="text-gray-300 dark:text-gray-600">|</span>
          <span className="tabular-nums">
            v{process.env.NEXT_PUBLIC_APP_VERSION}
          </span>
        </nav>
      </div>
    </footer>
  );
}
