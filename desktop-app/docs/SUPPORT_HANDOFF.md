# Support Handoff Script — Integration System v2.3.0

**Audience:** Internal Ops / Support Team
**Use:** When assisting a client remotely or in person

---

## Opening the session

Ask the client to open the app.

Do NOT ask them to describe what they see in technical terms.
Ask specific, guided questions only.

---

## Step 1 — Read the Home screen

Ask:

> "What does the Home screen show under **Recommended Next Step**?"

Use their answer to determine the correct path:

| Home screen shows | Action |
|---|---|
| "Fix This Now" (red) | Guide them to resolve the specific blocker shown |
| "Get Started" (blue) | Guide them through the relevant wizard |
| "All Clear" (green) | Guide them to run the next scheduled workflow |

---

## Step 2 — Guide through the correct wizard

If the client needs to download reports:

> "Click Download Reports on the left. Follow each step."

If the client needs to sync to QuickBooks:

> "Click Sync to QuickBooks on the left. Follow each step."

Each wizard validates before proceeding. If a step is blocked, the system will explain why.

---

## Step 3 — If an issue is not clear

Ask the client to:

1. Click **Recovery Center** in the sidebar
2. Click **Export Support Bundle**
3. Send you the exported file

Do NOT ask clients to:

- Open files manually
- Edit configuration
- Run any commands

All diagnosis is done through the exported bundle on your end.

---

## Step 4 — After resolving the issue

Ask the client to return to the Home screen and confirm the Recommended Next Step has changed.

If it has → issue is resolved.
If it has not → re-read the blocker detail and repeat from Step 1.

---

## Key Principles

- **Never ask clients to debug manually.** The system is designed to surface issues clearly. Use the Home screen and wizards as your primary diagnostic tool.
- **Blockers are by design.** If the system blocks an action, there is a real reason. Do not advise clients to bypass warnings without understanding the cause.
- **Export first, diagnose second.** When in doubt, get the support bundle before attempting any fix.

---

## Known Limits (v2.3.0)

- QuickBooks Desktop must be installed and configured on the client machine for QB Sync to work.
- Missing report files will block sync — this is intentional safety behavior, not a bug.
- Some warnings allow continuation at the operator's discretion. Confirm the client understands what they are accepting before proceeding.
