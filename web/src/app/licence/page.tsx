import fs from 'node:fs';
import path from 'node:path';
import Link from 'next/link';

import { PublicInformationShell } from '@/components/PublicInformationShell';
import { PUBLIC_TEXT_LINK_CLASS } from '@/lib/publicLinks';

export const dynamic = 'force-static';

/** Read-only copy of the exact software licence shipped in this source tree. */
export default function LicencePage() {
  const licence = fs.readFileSync(
    path.join(process.cwd(), 'legal-artifacts', 'LICENSE'),
    'utf8',
  );

  return (
    <PublicInformationShell
      lead='The exact open-source licence shipped with this Server build.'
      section='licence'
      title='Software licence'
    >
      <div className='space-y-6'>
        <div className='rounded-xl border border-blue-200 bg-blue-50 p-4 text-blue-950 dark:border-blue-900 dark:bg-blue-950/40 dark:text-blue-100'>
          <p>
            Masterplan Optimiser Server is distributed under the{' '}
            <a className={PUBLIC_TEXT_LINK_CLASS} href='https://www.gnu.org/licenses/agpl-3.0.html' rel='noopener noreferrer' target='_blank'>
              GNU Affero General Public License, version 3 only
            </a>. This software licence is separate from the controller&apos;s{' '}
            <Link className={PUBLIC_TEXT_LINK_CLASS} href='/privacy'>instance-specific notices</Link>{' '}
            and <Link className={PUBLIC_TEXT_LINK_CLASS} href='/terms'>terms</Link>.
          </p>
          <p className='mt-3 break-all text-sm'>
            Corresponding source for this exact build:{' '}
            <a
              className={PUBLIC_TEXT_LINK_CLASS}
              href={process.env.NEXT_PUBLIC_SOURCE_URL}
              rel='noreferrer'
              target='_blank'
            >
              {process.env.NEXT_PUBLIC_SOURCE_REPOSITORY_URL}@
              {process.env.NEXT_PUBLIC_SOURCE_REVISION}
            </a>
          </p>
        </div>

        <section aria-labelledby='licence-text-heading'>
          <h2 className='mb-3 text-xl font-semibold tracking-tight text-gray-900 dark:text-gray-100' id='licence-text-heading'>
            GNU Affero General Public License
          </h2>
          <div className='w-full overflow-hidden rounded-xl border border-gray-200 bg-gray-50 dark:border-gray-700 dark:bg-gray-900/50'>
            <pre className='m-0 w-full whitespace-pre-wrap break-words p-5 text-center font-sans text-sm leading-7 text-gray-700 dark:text-gray-300 sm:p-7'>
              {licence}
            </pre>
          </div>
        </section>
      </div>
    </PublicInformationShell>
  );
}
