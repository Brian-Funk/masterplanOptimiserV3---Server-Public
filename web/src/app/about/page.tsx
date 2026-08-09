import Link from 'next/link';

import {
  PublicInformationSection,
  PublicInformationShell,
} from '@/components/PublicInformationShell';
import { PUBLIC_TEXT_LINK_CLASS } from '@/lib/publicLinks';

const technologies = [
  ['Next.js', 'User interface framework', 'https://nextjs.org/'],
  ['React', 'Component model', 'https://react.dev/'],
  ['FastAPI', 'Backend API', 'https://fastapi.tiangolo.com/'],
  ['Google OR-Tools', 'Constraint-based schedule optimisation', 'https://developers.google.com/optimization'],
  ['SQLAlchemy', 'Database access', 'https://www.sqlalchemy.org/'],
  ['Tailwind CSS', 'Design system', 'https://tailwindcss.com/'],
  ['WebAuthn and passkeys', 'Passwordless authentication', 'https://developer.mozilla.org/en-US/docs/Web/API/Web_Authentication_API'],
] as const;

export default function AboutPage() {
  return (
    <PublicInformationShell
      lead='What Masterplan Optimiser does, how this build was produced, and who maintains the project.'
      section='about'
      title='About'
    >
      <PublicInformationSection title='Masterplan Optimiser'>
        <p>
          Masterplan Optimiser is an open-source scheduling and resource-allocation platform for
          event organisers. It combines constraint-based optimisation with a focused calendar
          interface for planning complex multi-day events.
        </p>
      </PublicInformationSection>

      <PublicInformationSection title='Technology'>
        <p>
          The application is built from reviewed open-source components. See the{' '}
          <Link className={PUBLIC_TEXT_LINK_CLASS} href='/third-party-notices'>
            complete third-party notices
          </Link>.
        </p>
        <ul className='grid list-none gap-3 p-0 sm:grid-cols-2'>
          {technologies.map(([name, purpose, href]) => (
            <li className='rounded-xl border border-gray-200 p-4 dark:border-gray-700' key={name}>
              <a className={PUBLIC_TEXT_LINK_CLASS} href={href} rel='noopener noreferrer' target='_blank'>
                {name}
              </a>
              <p className='mt-1 text-sm'>{purpose}</p>
            </li>
          ))}
        </ul>
      </PublicInformationSection>

      <PublicInformationSection title='Credits'>
        <p>
          Designed and developed by{' '}
          <a className={PUBLIC_TEXT_LINK_CLASS} href='https://github.com/Brian-Funk' rel='noopener noreferrer' target='_blank'>
            Brian Funk
          </a>.
        </p>
      </PublicInformationSection>

      <PublicInformationSection title='Build identity'>
        <dl className='grid gap-3 sm:grid-cols-2'>
          <div className='rounded-xl border border-gray-200 p-4 dark:border-gray-700'>
            <dt className='text-sm text-gray-500 dark:text-gray-400'>Web application</dt>
            <dd className='mt-1 font-semibold text-gray-900 dark:text-gray-100'>
              v{process.env.NEXT_PUBLIC_APP_VERSION}
            </dd>
          </div>
          <div className='rounded-xl border border-gray-200 p-4 dark:border-gray-700'>
            <dt className='text-sm text-gray-500 dark:text-gray-400'>Corresponding source</dt>
            <dd className='mt-1 break-all text-sm'>
              <a
                className={PUBLIC_TEXT_LINK_CLASS}
                href={process.env.NEXT_PUBLIC_SOURCE_URL}
                rel='noreferrer'
                target='_blank'
              >
                {process.env.NEXT_PUBLIC_SOURCE_REPOSITORY_URL}@
                {process.env.NEXT_PUBLIC_SOURCE_REVISION}
              </a>
            </dd>
          </div>
        </dl>
      </PublicInformationSection>
    </PublicInformationShell>
  );
}
