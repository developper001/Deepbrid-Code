# Deepbrid Code project handoff

Deepbrid Code is a local-first Python/Tkinter coding assistant. It opens a
project folder, lets users select text and file attachments, and sends only
explicitly selected context to Deepbrid AI.

## Current architecture

- Deepbrid AI chat uses the documented `/api/v1/ai` API. A Premium API key is
  required and is stored in the operating-system credential store.
- The app loads available models and assistants, sends Web-search preferences
  and file attachments, and supports listing, searching, opening, and creating
  saved conversations.
- No browser automation or browser login flow is part of the application.
- Application preferences are stored in SQLite. A legacy plaintext API key is
  migrated to the credential store on first access.
- The repository is `https://github.com/developper001/Deepbrid-Code`.

## Privacy and implementation constraints

- Never put API keys or other credentials in source control, logs, or prompts.
- Send only context the user explicitly selects. Keep selected content visible
  before sending, and avoid secrets and generated/dependency folders by default.
- Do not modify project files without an explicit user action.
- Use documented API endpoints and report API failures explicitly.

## Validation

Run the test suite with `python -m unittest discover -s tests -v`.
