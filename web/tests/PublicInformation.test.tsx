import fs from 'node:fs';
import path from 'node:path';

import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ArtifactMarkdown } from '@/components/ArtifactMarkdown';
import { PublicInformationShell } from '@/components/PublicInformationShell';

vi.mock('@/components/Logo', () => ({
  Logo: () => <div aria-label='Masterplan Optimiser logo'>Logo</div>,
}));

describe('public information pages', () => {
  it('uses the shared branded shell and calm public navigation', () => {
    render(
      <PublicInformationShell lead='Exact project information.' section='security' title='Security'>
        <p>Security content.</p>
      </PublicInformationShell>,
    );

    expect(screen.getByLabelText('Masterplan Optimiser logo')).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 1, name: 'Security' })).toBeInTheDocument();
    expect(screen.getByRole('navigation', { name: 'Public information' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Security' })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('link', { name: 'Legal centre' })).toHaveAttribute('href', '/privacy');
  });

  it('renders bundled Markdown tables as accessible responsive tables', () => {
    render(
      <ArtifactMarkdown
        markdown={'# Notices\n\n## Packages\n\n| Package | Licence | Upstream |\n|---|---|---|\n| example | MIT | https://example.invalid/ |'}
      />,
    );

    expect(screen.getByRole('heading', { level: 2, name: 'Packages' })).toBeInTheDocument();
    expect(screen.getByRole('table', { name: 'Packages' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'Package' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'https://example.invalid/' })).toHaveAttribute(
      'href',
      'https://example.invalid/',
    );
  });

  it('uses the self-hosted Source Sans 3 family globally and in immutable notices', () => {
    const globalCss = fs.readFileSync(path.join(process.cwd(), 'src', 'app', 'globals.css'), 'utf8');
    const governanceSource = fs.readFileSync(
      path.join(process.cwd(), '..', 'backend', 'app', 'api', 'v1', 'governance.py'),
      'utf8',
    );

    expect(globalCss).toContain('font-family: \'Source Sans 3\'');
    expect(globalCss).not.toContain('var(--font-source-sans)');
    expect(governanceSource).toContain('@font-face{font-family:\'Source Sans 3\'');
    expect(governanceSource).toContain('/fonts/source-sans-3-latin-400-normal.woff2');
  });
});
