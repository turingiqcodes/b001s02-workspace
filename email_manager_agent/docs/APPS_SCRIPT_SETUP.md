# Google Apps Script bridge setup

The backend reaches Gmail, Google Calendar and Google Drive through a small Apps Script
Web App that runs as you. You deploy it once from your own Google account. It takes about
ten minutes.

You need:
- the files in `apps_script/`: `Code.gs` and `appsscript.json`
- the `.env` file you created from `.env.example` in the project root

## 1. Create the project

1. Go to <https://script.google.com> and click **New project**.
2. Rename it (click "Untitled project"), for example to `Email Manager bridge`.

## 2. Show the manifest and paste the files

1. Open **Project Settings** (the gear icon on the left) and tick
   **Show "appsscript.json" manifest file in editor**.
2. Go back to the **Editor** (the `< >` icon).
3. Open `appsscript.json` and replace everything in it with the contents of
   `apps_script/appsscript.json`.
4. Open `Code.gs` and replace everything in it with the contents of `apps_script/Code.gs`.
5. Press **Ctrl+S** (or **Cmd+S**) to save.

The manifest turns on the **Gmail API**, **Google Calendar API** and **Drive API** advanced
services and asks for these permissions:

| Scope | Why it's needed |
|---|---|
| `gmail.modify` | Read, search and send email, manage drafts, and add or remove labels. It does not allow permanent deletion, and the bridge has no delete or trash action anyway. |
| `calendar.events` | List, create, move and cancel events. |
| `drive` | Search all your Drive files, move existing files into folders, and save attachments. The narrower `drive.file` scope only sees files this script created, so "find PDFs named invoice" and "move this file" wouldn't work with it. The bridge has no delete or trash action for files. |

You don't need to add anything under **Services (+)**. The manifest already turns on the
three services.

## 3. Create the shared secret

The backend and the bridge share a secret. The bridge rejects every request that doesn't
include it.

1. Generate a secret:
   ```bash
   python -c "import secrets;print(secrets.token_urlsafe(32))"
   ```
2. In Apps Script, open **Project Settings → Script Properties → Add script property**:
   - Property: `BRIDGE_SHARED_SECRET`
   - Value: the secret you generated
   Click **Save script properties**.
3. In the project's `.env`, set the same value:
   ```
   APPS_SCRIPT_SHARED_SECRET=<the same secret>
   ```

The two values must match exactly. Leave out quotes and surrounding spaces. Don't put the
secret in `Code.gs`.

## 4. Run `checkSetup` and authorize

1. In the editor, choose **checkSetup** in the function dropdown at the top, then click **Run**.
2. Google asks you to authorize the project. Choose your account. Google warns that the app
   isn't verified, because it's your own unpublished script, so click **Advanced → Go to
   Email Manager bridge (unsafe)** and then **Allow**.
3. Open the **Execution log**. You should see lines like:
   ```
   secret: set (43 characters)
   gmail: ok (you@gmail.com)
   calendar: ok
   drive: ok
   Setup looks good. Deploy as a Web App next.
   ```
   The secret's value itself is never logged. If any line says `missing` or `error`, fix
   that item and run `checkSetup` again.

## 5. Deploy as a Web App

1. Click **Deploy → New deployment**.
2. Click the gear next to **Select type** and choose **Web app**.
3. Fill in:
   - **Description**: `v1`, or anything you like
   - **Execute as**: **Me**
   - **Who has access**: **Anyone**
4. Click **Deploy**, then copy the **Web app URL**. It ends in `/exec`.
5. In `.env`, set:
   ```
   APPS_SCRIPT_URL=https://script.google.com/macros/s/XXXXXXXX/exec
   ```
6. Restart the backend, open the app, and click **Check connection**. The Google part of the
   status bar should turn green.

The backend calls the Web App without a Google login, which is why access is set to
"Anyone". Anyone with the URL can reach the bridge, but every request must carry the shared
secret, and the bridge validates every input again before it acts. Keep the URL and the
secret private.

## 6. After you change `Code.gs`

The `/exec` URL keeps running the version you deployed until you deploy a new one:

**Deploy → Manage deployments →** select the deployment **→ Edit (pencil) → Version: New
version → Deploy**.

The URL stays the same. If you changed the scopes in `appsscript.json`, run `checkSetup`
again first so Google asks for the new permissions.

## Troubleshooting

| Symptom | Fix |
|---|---|
| "The Google bridge did not return JSON" | The deployment must use **Who has access: Anyone**, and `APPS_SCRIPT_URL` must be the `/exec` URL, not `/dev` or the editor URL. |
| "Invalid shared secret" | `APPS_SCRIPT_SHARED_SECRET` in `.env` and the `BRIDGE_SHARED_SECRET` script property differ. Copy the value again without quotes or spaces, then restart the backend. |
| `not_configured` | The `BRIDGE_SHARED_SECRET` script property is missing. Add it (step 3). You don't need to redeploy, because script properties are read at request time. |
| "permission" or "authorization" errors | Run `checkSetup` (or any function) in the editor and approve the prompt. After changing scopes, deploy a new version. |
| Google is slow or times out | Apps Script can take a few seconds to start. Try again, or raise `BRIDGE_TIMEOUT_SECONDS` in `.env`. For a write that timed out, check Gmail, Calendar or Drive before retrying, because it may have gone through. |
| Quota errors | Consumer Google accounts have daily Apps Script quotas, for example for email sending. Wait until the next day or reduce usage. |
| Times look wrong | Set `USER_TIMEZONE` in `.env`. The bridge sends and receives times with explicit offsets, so the script's own time zone (`Etc/UTC`) doesn't matter. |

## Running the bridge tests

The tests run `Code.gs` in Node with fake Gmail, Calendar and Drive services. They never
contact Google.

```bash
cd apps_script
node --test tests/*.test.js     # or: npm test
```
