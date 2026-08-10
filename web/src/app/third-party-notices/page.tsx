import fs from 'node:fs';
import path from 'node:path';
import Link from 'next/link';

import { ArtifactMarkdown } from '@/components/ArtifactMarkdown';
import { PublicInformationShell } from '@/components/PublicInformationShell';
import { PUBLIC_TEXT_LINK_CLASS } from '@/lib/publicLinks';

export const dynamic = 'force-static';

/** Read-only third-party notices shipped with this source tree. */
export default function ThirdPartyNoticesPage() {
  const repositoryUrl = process.env.NEXT_PUBLIC_SOURCE_REPOSITORY_URL;
  const notices = fs.readFileSync(
    path.join(process.cwd(), 'legal-artifacts', 'THIRD-PARTY-NOTICES.md'),
    'utf8',
  );

  return (
    <PublicInformationShell
      lead='The reviewed dependency and bundled-asset inventory tied to this exact source build.'
      section='third-party-notices'
      title='Third-party notices'
    >
      <div className='mb-8 rounded-xl border border-gray-200 bg-gray-50 p-4 text-gray-600 dark:border-gray-700 dark:bg-gray-700/40 dark:text-gray-300'>
        This inventory is generated from committed dependency locks.{' '}
        {repositoryUrl ? (
          <a className={PUBLIC_TEXT_LINK_CLASS} href={`${repositoryUrl}/releases`} rel='noopener noreferrer' target='_blank'>
            Release SBOMs
          </a>
        ) : 'Release SBOMs'}{' '}
        remain authoritative for operating-system packages and exact container digests. See the{' '}
        <Link className={PUBLIC_TEXT_LINK_CLASS} href='/licence'>project licence</Link>{' '}
        for the software&apos;s licensing terms.
      </div>
      <ArtifactMarkdown
        markdown={notices}
        sourceBaseUrl={process.env.NEXT_PUBLIC_SOURCE_URL}
      />
    </PublicInformationShell>
  );
}
