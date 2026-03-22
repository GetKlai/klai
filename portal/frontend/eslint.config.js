import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist', 'src/paraglide', 'src/routeTree.gen.ts']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommendedTypeChecked,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
    rules: {
      'no-console': ['error', { allow: ['warn', 'error'] }],
      // TanStack Router uses async functions in route config (beforeLoad, loader)
      '@typescript-eslint/no-misused-promises': [
        'error',
        { checksVoidReturn: { attributes: false } },
      ],
      // TanStack Router's redirect() is thrown intentionally (special object, not Error)
      '@typescript-eslint/only-throw-error': 'off',
      // Empty interfaces are the standard shadcn/ui pattern for extensible component props
      '@typescript-eslint/no-empty-object-type': 'off',
      // Underscore-prefixed names are intentionally unused (standard convention)
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }],
      // Syncing server data to local form state via useEffect is a common, intentional React pattern
      'react-hooks/set-state-in-effect': 'off',
      // no-unsafe-* requires a fully typed API client — disable until API types are generated
      '@typescript-eslint/no-unsafe-assignment': 'off',
      '@typescript-eslint/no-unsafe-call': 'off',
      '@typescript-eslint/no-unsafe-return': 'off',
      '@typescript-eslint/no-unsafe-member-access': 'off',
      '@typescript-eslint/no-unsafe-argument': 'off',
    },
  },
  // shadcn/ui components export variants alongside the component — this is intentional
  {
    files: ['src/components/ui/**/*.{ts,tsx}', 'src/lib/locale.tsx'],
    rules: {
      'react-refresh/only-export-components': 'off',
    },
  },
])
