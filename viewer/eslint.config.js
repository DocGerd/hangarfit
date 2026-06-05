// Flat ESLint config (ESLint 10, `defineConfig` idiom). Basic JS-recommended rules
// everywhere; Node globals for the build/config scripts; the typescript-eslint
// recommended set scoped to the TS sources. Non-type-checked (syntactic) — tsc
// (`npm run typecheck`) owns type correctness; #439/#440 may graduate `src` to
// recommendedTypeChecked once real modules exist.
import { defineConfig } from "eslint/config";
import js from "@eslint/js";
import globals from "globals";
import tseslint from "typescript-eslint";

export default defineConfig([
  { ignores: ["node_modules/"] },
  js.configs.recommended,
  {
    files: ["**/*.mjs", "**/*.js"],
    languageOptions: { globals: globals.node },
  },
  {
    files: ["src/**/*.ts", "test/**/*.ts"],
    extends: [tseslint.configs.recommended],
  },
]);
