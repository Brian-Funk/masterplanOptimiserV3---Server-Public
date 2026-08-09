import {
  PublicInformationSection,
  PublicInformationShell,
} from '@/components/PublicInformationShell';

const technologies = [
  ['Next.js and React', 'User interface'],
  ['FastAPI', 'Backend API'],
  ['Google OR-Tools', 'Constraint-based schedule optimisation'],
  ['SQLAlchemy', 'Database access'],
  ['Tailwind CSS', 'Design system'],
  ['WebAuthn and passkeys', 'Passwordless authentication'],
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
        <p>The application is built from reviewed open-source components.</p>
        <ul className='grid list-none gap-3 p-0 sm:grid-cols-2'>
          {technologies.map(([name, purpose]) => (
            <li className='rounded-xl border border-gray-200 p-4 dark:border-gray-700' key={name}>
              <strong className='text-gray-900 dark:text-gray-100'>{name}</strong>
              <p className='mt-1 text-sm'>{purpose}</p>
            </li>
          ))}
        </ul>
      </PublicInformationSection>

      <PublicInformationSection title='Credits'>
        <p>
          Designed and developed by <strong className='text-gray-900 dark:text-gray-100'>Brian Funk</strong>.
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
