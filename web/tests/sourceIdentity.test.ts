import sourceIdentity from "../source-identity.cjs";
import { describe, expect, it } from "vitest";

type ResolveSourceIdentity = (options: {
  env?: Record<string, string | undefined>;
  repositoryRoot: string;
}) => {
  repositoryUrl: string;
  revision: string;
  sourceUrl: string;
};

const { normaliseRepositoryUrl, validateSourceUrl } = sourceIdentity;
const resolveSourceIdentity = sourceIdentity.resolveSourceIdentity as unknown as
  ResolveSourceIdentity;
const revision = "a".repeat(40);

describe("public corresponding-source identity", () => {
  it("normalises GitHub SSH remotes without credentials", () => {
    expect(normaliseRepositoryUrl("git@github.com:example/fork.git")).toBe(
      "https://github.com/example/fork",
    );
  });

  it("binds a GitHub repository to one exact commit", () => {
    expect(
      resolveSourceIdentity({
        repositoryRoot: ".",
        env: {
          NODE_ENV: "test",
          MP_PUBLIC_SOURCE_REPOSITORY_URL: "https://github.com/example/fork.git",
          MP_PUBLIC_SOURCE_REVISION: revision,
        },
      }),
    ).toEqual({
      repositoryUrl: "https://github.com/example/fork",
      revision,
      sourceUrl: `https://github.com/example/fork/tree/${revision}`,
    });
  });

  it("allows a modified non-GitHub deployment to provide its exact source URL", () => {
    expect(
      resolveSourceIdentity({
        repositoryRoot: ".",
        env: {
          NODE_ENV: "test",
          MP_PUBLIC_SOURCE_REPOSITORY_URL: "https://code.example.org/team/fork",
          MP_PUBLIC_SOURCE_REVISION: revision,
          MP_PUBLIC_SOURCE_URL: `https://code.example.org/team/fork/-/tree/${revision}`,
        },
      }).sourceUrl,
    ).toContain(revision);
  });

  it("rejects floating revisions, credentials and mismatched source links", () => {
    expect(() =>
      resolveSourceIdentity({
        repositoryRoot: ".",
        env: {
          NODE_ENV: "test",
          MP_PUBLIC_SOURCE_REPOSITORY_URL: "https://github.com/example/fork",
          MP_PUBLIC_SOURCE_REVISION: "main",
        },
      }),
    ).toThrow(/exact 40-character Git commit SHA/);
    expect(() =>
      normaliseRepositoryUrl("https://token@example.org/team/fork"),
    ).toThrow(/credential-free HTTPS/);
    expect(() =>
      validateSourceUrl("https://example.org/team/fork/tree/main", revision),
    ).toThrow(/contain the exact revision/);
  });
});
