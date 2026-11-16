# Learning interface refresh

The app serves an SQL beginner who already understands operational ERP screens.
The visual direction is a calm training workbench: warm neutral surfaces, dark ink, teal learning actions, and amber progress accents.
The memorable connection is between the familiar Kestrel transaction and the tables and SQL behind it.
The classic purchase-order interior stays intentionally dense; the learning pages prioritize reading and practice.

## Checkable design contract

- The top navigation exposes Lessons, ERP Explorer, Notebook, Schema, and Playground on every page.
- The lesson dashboard offers company orientation first, a visible learning path, and progress based on actual completed exercises.
- Lesson pages separate explanation from practice, expose an exercise outline, and keep Run, Check, hints, and result feedback close to the editor.
- ERP Explorer opens from lessons in a new tab without replacing the unsaved editor.
- Shared styles improve notebook, schema, playground, transaction, and result layouts without changing database behavior.
- All assets load locally, and no new runtime dependency is needed.
- Primary flows remain keyboard-operable with visible focus, labeled inputs, readable contrast, and reduced-motion support.
- At 390x844 and 1440x1000, page content must not overflow horizontally; wide data tables use their own scroll regions.

## Content-state coverage

Loading uses the existing HTMX request lifecycle with a visible working indicator and preserves the current layout.
Empty explorer results offer a clear search reset; empty notebooks retain the create action.
Query errors preserve the editor and offer retry through Run or Check.
Partial query results retain their existing row-limit notice; individual ERP query errors remain visible beside successful sections.
Successful exercise checks retain the pass banner and persisted completion.
Permission gates are not applicable because this is a single-user local lab with no accounts; the lesson versus practice database distinction remains visible.

## Verification

Use local screenshots and a real Edge browser to review the dashboard, introduction, lesson editor, explorer, notebook, schema, playground, and classic purchase order.
Exercise search, paging, new-tab exploration, query success/error/empty/partial states, and keyboard focus.
The final evidence record and browser scripts are stored with this chat's local visualization artifacts.

## SQL workbench

Keep lesson text readable beside a docked editor at 1440x1000; on a 390x844 phone, use a full-screen panel with a visible Minimize action.
The workbench uses the existing local editor and read-only query/check APIs.
Opening an exercise carries its instructions and identity into the panel; free queries have no Check action.
Separate drafts survive same-tab navigation and reload through session storage, with a visible warning when storage is unavailable.
Floating windows support pointer and keyboard movement/resizing, stay within the viewport, and retain their geometry when minimized.
Run and Check retain the existing persistent exercise progress behavior.
Loading disables duplicate actions and exercise switching; network errors preserve SQL and allow retry.
Empty and truncated results use the existing query feedback, and hints remain bound to the selected exercise.

## Dark palette

Dark mode uses neutral charcoal surfaces, soft blue links, subdued blue action buttons, and distinct editor backgrounds.
Success and warning colors remain reserved for feedback; SQL keywords, strings, and numbers use separate readable colors.
The classic purchase-order layout adopts dark surfaces while retaining its blue titlebar, warm input field, and amber item selection.
Verify desktop and mobile layouts at 1440x1000 and 390x844, including editor cursor/selection, results, relationship keys, and the floating workbench.
Normal text and primary action labels must meet a 4.5:1 contrast ratio on their actual backgrounds.
