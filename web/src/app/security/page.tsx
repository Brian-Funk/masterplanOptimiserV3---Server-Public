import fs from 'node:fs';
import path from 'node:path';

import { ArtifactMarkdown } from '@/components/ArtifactMarkdown';
import { PublicInformationShell } from '@/components/PublicInformationShell';

export const dynamic = 'force-static';

/** Public security-contact policy, excluding internal engineering reports. */
export default function SecurityPage() {
  const securityPolicy = fs.readFileSync(
    path.join(process.cwd(), 'legal-artifacts', 'SECURITY.md'),
    'utf8',
  );

  return (
    <PublicInformationShell
      lead='How to report a vulnerability safely and which releases receive security fixes.'
      section='security'
      title='Security'
    >
      <div className='mb-8 rounded-xl border border-amber-200 bg-amber-50 p-4 text-amber-950 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-100'>
        Do not send credentials, recovery material, personal data, production databases, or
        private incident evidence through a public issue.
      </div>
      <ArtifactMarkdown
        markdown={securityPolicy}
        sourceBaseUrl={process.env.NEXT_PUBLIC_SOURCE_URL}
      />
    </PublicInformationShell>
  );
}
