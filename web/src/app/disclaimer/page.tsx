import Link from 'next/link';

import {
  PublicInformationSection,
  PublicInformationShell,
} from '@/components/PublicInformationShell';
import { PUBLIC_TEXT_LINK_CLASS } from '@/lib/publicLinks';

export default function DisclaimerPage() {
  return (
    <PublicInformationShell
      lead='Important limits on software-generated schedules, external services, and operational decisions.'
      section='disclaimer'
      title='Disclaimer'
    >
      <PublicInformationSection title='Advisory output'>
        <p>
          Masterplan Optimiser assists event organisers with scheduling and resource allocation.
          Optimised schedules, assignments, and suggestions are advisory and should be reviewed by
          a qualified person before they are acted upon.
        </p>
      </PublicInformationSection>

      <PublicInformationSection title='No warranty'>
        <p>
          The software is provided <strong className='text-gray-900 dark:text-gray-100'>as is</strong>,
          without warranty of any kind, express or implied, including warranties of merchantability,
          fitness for a particular purpose, and non-infringement. See the{' '}
          <Link className={PUBLIC_TEXT_LINK_CLASS} href='/licence'>software licence</Link>{' '}
          for the exact terms shipped with this build.
        </p>
      </PublicInformationSection>

      <PublicInformationSection title='Limitation of liability'>
        <p>
          To the extent permitted by applicable law, the author is not liable for indirect,
          incidental, special, consequential, or punitive damages—including loss of data,
          scheduling errors, or missed deadlines—arising from use of the software.
        </p>
      </PublicInformationSection>

      <PublicInformationSection title='Third-party services'>
        <p>
          A deployment may connect to services selected by its operator. The published{' '}
          <Link className={PUBLIC_TEXT_LINK_CLASS} href='/processors'>processor register</Link>{' '}
          identifies the providers declared for this instance. Availability, accuracy, security,
          and the <Link className={PUBLIC_TEXT_LINK_CLASS} href='/terms'>applicable terms</Link>{' '}
          remain the responsibility of those providers and the self-hosting controller.
        </p>
      </PublicInformationSection>
    </PublicInformationShell>
  );
}
