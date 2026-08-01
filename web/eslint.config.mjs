import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTypeScript from "eslint-config-next/typescript";

/**
 * Lint the static Next.js application with the supported flat configuration.
 */
export default defineConfig([
  ...nextVitals,
  ...nextTypeScript,
  {
    rules: {
      // Existing effects intentionally synchronise URL, storage and API state.
      // These compiler-oriented rules will be enabled incrementally after the
      // framework migration rather than changing runtime behaviour here.
      "react-hooks/immutability": "off",
      "react-hooks/set-state-in-effect": "off",
    },
  },
  {
    files: ["next.config.js"],
    rules: {
      "@typescript-eslint/no-require-imports": "off",
    },
  },
  globalIgnores([
    ".next/**",
    "out/**",
    "build/**",
    "docs/api/**",
    "next-env.d.ts",
  ]),
]);
