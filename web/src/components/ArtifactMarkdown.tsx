import type { ReactNode } from "react";

import { PUBLIC_TEXT_LINK_CLASS } from "@/lib/publicLinks";

type Block =
  | { kind: "heading"; text: string }
  | { kind: "paragraph"; text: string }
  | { kind: "table"; caption: string; headers: string[]; rows: string[][] };

/** Render the small, reviewed Markdown subset used by bundled public artifacts. */
export function ArtifactMarkdown({
  markdown,
  sourceBaseUrl,
}: {
  markdown: string;
  sourceBaseUrl?: string;
}) {
  const blocks = parseArtifact(markdown);

  return (
    <div className="space-y-7">
      {blocks.map((block, index) => {
        if (block.kind === "heading") {
          return (
            <h2
              className="border-t border-gray-200 pt-7 text-xl font-semibold tracking-tight text-gray-900 first:border-t-0 first:pt-0 dark:border-gray-700 dark:text-gray-100"
              key={`${block.text}-${index}`}
            >
              {block.text}
            </h2>
          );
        }
        if (block.kind === "paragraph") {
          return (
            <p className="max-w-[78ch] whitespace-pre-line leading-7 text-gray-600 dark:text-gray-300" key={index}>
              <InlineMarkdown sourceBaseUrl={sourceBaseUrl}>{block.text}</InlineMarkdown>
            </p>
          );
        }
        return (
          <div className="overflow-x-auto rounded-xl border border-gray-200 dark:border-gray-700" key={`${block.caption}-${index}`}>
            <table className="w-full min-w-[720px] border-collapse text-left text-sm">
              <caption className="sr-only">{block.caption}</caption>
              <thead className="bg-gray-50 dark:bg-gray-700/60">
                <tr>
                  {block.headers.map((header) => (
                    <th className="border-b border-gray-200 px-4 py-3 font-semibold text-gray-900 dark:border-gray-700 dark:text-gray-100" key={header} scope="col">
                      <InlineMarkdown sourceBaseUrl={sourceBaseUrl}>{header}</InlineMarkdown>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
                {block.rows.map((row, rowIndex) => (
                  <tr className="align-top odd:bg-white even:bg-gray-50/60 dark:odd:bg-gray-800 dark:even:bg-gray-700/20" key={rowIndex}>
                    {row.map((cell, cellIndex) => (
                      <td className="px-4 py-2.5 text-gray-600 dark:text-gray-300" key={cellIndex}>
                        <InlineMarkdown sourceBaseUrl={sourceBaseUrl}>{cell}</InlineMarkdown>
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
      })}
    </div>
  );
}

function parseArtifact(markdown: string): Block[] {
  const lines = markdown.replaceAll("\r\n", "\n").split("\n");
  const blocks: Block[] = [];
  let index = 0;
  let currentSection = "Bundled artifact inventory";

  while (index < lines.length) {
    const line = lines[index].trim();
    if (!line) {
      index += 1;
      continue;
    }
    if (line.startsWith("# ")) {
      index += 1;
      continue;
    }
    if (line.startsWith("## ")) {
      currentSection = line.slice(3).trim();
      blocks.push({ kind: "heading", text: currentSection });
      index += 1;
      continue;
    }
    if (isTableRow(line) && index + 1 < lines.length && isTableDivider(lines[index + 1])) {
      const headers = parseTableRow(line);
      const rows: string[][] = [];
      index += 2;
      while (index < lines.length && isTableRow(lines[index].trim())) {
        rows.push(parseTableRow(lines[index].trim()));
        index += 1;
      }
      blocks.push({ kind: "table", caption: currentSection, headers, rows });
      continue;
    }

    const paragraph = [line];
    index += 1;
    while (index < lines.length) {
      const next = lines[index].trim();
      if (!next || next.startsWith("#") || (isTableRow(next) && index + 1 < lines.length && isTableDivider(lines[index + 1]))) break;
      paragraph.push(next);
      index += 1;
    }
    blocks.push({ kind: "paragraph", text: paragraph.join(" ") });
  }
  return blocks;
}

function isTableRow(line: string) {
  return line.startsWith("|") && line.endsWith("|");
}

function isTableDivider(line: string) {
  return /^\s*\|(?:\s*:?-+:?\s*\|)+\s*$/.test(line);
}

function parseTableRow(line: string) {
  return line
    .slice(1, -1)
    .split("|")
    .map((cell) => cell.trim());
}

function InlineMarkdown({ children, sourceBaseUrl }: { children: string; sourceBaseUrl?: string }) {
  const tokens = children.split(/(`[^`]+`|\[[^\]]+\]\([^)]+\)|https?:\/\/[^\s|]+)/g);
  return tokens.map((token, index): ReactNode => {
    if (token.startsWith("`") && token.endsWith("`")) {
      return <code className="rounded bg-gray-100 px-1.5 py-0.5 text-xs text-gray-800 dark:bg-gray-700 dark:text-gray-200" key={index}>{token.slice(1, -1)}</code>;
    }
    const markdownLink = token.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
    const rawHref = markdownLink?.[2] || (/^https?:\/\//.test(token) ? token : null);
    if (rawHref) {
      const href = resolveArtifactHref(rawHref.replace(/[.,;:]$/, ""), sourceBaseUrl);
      const label = markdownLink?.[1] || token;
      return <a className={`${PUBLIC_TEXT_LINK_CLASS} break-words`} href={href} key={index} rel="noopener noreferrer" target="_blank"><LinkLabel text={label} /></a>;
    }
    return token;
  });
}

function resolveArtifactHref(href: string, sourceBaseUrl?: string) {
  if (/^https?:\/\//.test(href) || href.startsWith("#") || href.startsWith("/")) return href;
  if (!sourceBaseUrl) return href;
  return `${sourceBaseUrl.replace(/\/$/, "")}/${href.replace(/^\.\//, "")}`;
}

function LinkLabel({ text }: { text: string }) {
  if (text.startsWith("**") && text.endsWith("**") && text.length > 4) {
    return <strong>{text.slice(2, -2)}</strong>;
  }
  return text;
}
