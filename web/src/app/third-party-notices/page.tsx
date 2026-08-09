import fs from 'node:fs';
import path from 'node:path';

import { ArtifactMarkdown } from '@/components/ArtifactMarkdown';
import { PublicInformationShell } from '@/components/PublicInformationShell';

export const dynamic = 'force-static';

/** Read-only third-party notices shipped with this source tree. */
export default function ThirdPartyNoticesPage() {
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
        This inventory is generated from committed dependency locks. Release SBOMs remain
        authoritative for operating-system packages and exact container digests.
      </div>
      <ArtifactMarkdown
        markdown={notices}
        sourceBaseUrl={process.env.NEXT_PUBLIC_SOURCE_URL}
      />
    </PublicInformationShell>
  );
}
