# UI

- Drive the real UI in a browser (Playwright inside the sandbox). Cover the happy path, then each failure the API can return: show the error, keep user input, offer retry.
- States: loading, empty, error, partial data, very long text, many items, unauthorized, expired session mid-action, slow network, double click on submit.
- Navigation: refresh mid-flow, back and forward, deep link straight to the page, a second tab doing the same thing.
- Accessibility: keyboard-only path, focus order and visible focus, labels for inputs, roles for custom controls, contrast, screen reader names for icon buttons.
- Responsive layouts at a small, medium and large width. Text overflow and truncation.
- Auth storage: where tokens live, what survives a reload or a new tab, and what logout clears.
- Take screenshots of every changed screen, before and after. Attach them to the report.
