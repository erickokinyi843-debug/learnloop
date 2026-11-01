# Learnloop

Learnloop is a small Python/SQLite learning app. The backend serves the existing HTML/CSS/JavaScript front end and enforces account roles on every protected API request.

## Run locally

Python 3.10 or newer is required. The server uses only the Python standard library.

In PowerShell, create a one-time admin setup key, keep it private, and start the server. By default, the server listens on all network interfaces so other devices on your private Wi-Fi/LAN can connect:

```powershell
$env:ADMIN_SETUP_KEY = [guid]::NewGuid().ToString("N")
$env:ADMIN_SETUP_KEY
$env:LEARNLOOP_HOST = "0.0.0.0"
python server.py
```

Open <http://127.0.0.1:8000> on the host computer. In the server output, look for the local network address (for example `http://192.168.1.25:8000`) and give that address to students on the same Wi-Fi/LAN. Choose **Create student account** for a new student or sign in with an existing student account. The host computer and student devices must be on the same reachable network; guest Wi-Fi or client isolation may block access. If Windows Firewall prompts, allow Python on **private networks only**.

Open <http://127.0.0.1:8000/admin> on the host computer, or select **Admin sign in** on the student sign-in screen, to access the separate admin sign-in page. For initial setup, enter the setup key printed in PowerShell; setup is one-time and the server allows only one main admin account. After setup, sign in with that admin email and password. Keep the setup key private; remove it from the environment after setup if you do not need it for a database reset. Admins can invite minor admins from the dashboard. The `/admin` page is a separate entry point, not a substitute for account authentication; admin APIs continue to enforce roles and permissions on the server. To listen on this computer only, set `$env:LEARNLOOP_HOST = "127.0.0.1"` before starting.

Student accounts are created through **Create student account**. Sign-up collects the school and grade/level; the grade field suggests the app's Kenyan CBC levels and also accepts a custom school level. Students can update those details later from **School & grade**. Subject cards are selected from the CBC learning-area catalogue for the chosen level. Use **Explore** or **View all** to jump to the subject catalogue. CBC learning areas from PP1 through Grade 12 open a dedicated, content-only revision view with expanded topic coverage, explanations, worked examples, guided activities, and self-check questions. The guides are original supplementary materials: topic sequencing and depth can vary by school and pathway, and they are not official KICD curriculum designs or a complete syllabus. Confirm coverage against the current design and your school's approved programme. 8-4-4 materials are not included yet. Admins can choose a suggested subject or enter a custom subject when publishing classes or uploading notes and exams. Their passwords are stored as salted PBKDF2 hashes. Session cookies are HTTP-only and same-site; student progress, uploaded files, settings, profiles, and accounts are stored in the `data` directory.

The main admin can create student accounts from the dashboard, suspend or restore access, and permanently delete individual accounts. Deleting an account also deletes that learner's progress and group memberships. The **Study & grade groups** panel creates study groups and grade/class groups, adds or removes specific active students, and can delete an entire group without deleting its student accounts. Grade/class group membership is restricted to students whose profile grade matches the group's grade.

The main admin dashboard's **Student study materials** overview inventories every built-in revision guide, uploaded note or exam, and published video class, including the subject, grade, and availability scope. Search and filter the inventory to review what learners can access.

## Video classes and helper admins

The admin dashboard can publish a recorded class with a CBC grade, subject, optional schedule, and description. Video links are restricted to valid YouTube and Vimeo URLs; students see classes for their grade. The server generates an embed URL from the validated video ID rather than embedding arbitrary links.

Use **Task admins & invites** to create a one-time invitation for an existing student account or a new helper. Select one or more grades and permitted tasks (video classes, adding/removing notes and exams, and/or learner status management). Helpers with notes/exams access can manage resources only within their assigned grades. You may also assign specific existing video sessions. Copy and share the invitation link privately; it expires after seven days and can only be accepted once using the invited email. No email delivery service is configured. Helpers do not receive owner access to platform settings, competition monitoring, or other helpers, and each API request enforces their task and grade/session scopes. Revoking a helper ends their active sessions.

Senior-grade CBC learning areas shown are a broad catalogue; subject combinations and pathway options can vary by school.

## Reset a forgotten admin password

On the computer hosting Learnloop, open PowerShell in this project folder and run:

```powershell
python reset_admin_password.py
```

Type the existing admin email to confirm, then enter and confirm a new password in the hidden console prompts. The reset updates the existing account, keeps student accounts and learning data, and signs out current admin sessions. Sign in with the new password. Do not delete `data\learnloop.sqlite3`.

Student sign-up and initial admin setup require confirming the password. Use the **Show** control next to either password field to reveal it temporarily.

## API overview

- `POST /api/auth/signup`, `POST /api/auth/login`, `POST /api/auth/logout`
- `POST /api/auth/admin-setup` (requires the server's one-time setup key)
- `GET /api/auth/me`, `GET /api/auth/status`
- `GET /api/curriculum`, `GET /api/classes`
- `POST /api/invites/validate`, `POST /api/auth/invites/signup`, `POST /api/auth/invites/accept`
- `GET /api/student/progress`, `GET /api/student/settings`, `POST /api/student/activity`
- `PATCH /api/student/profile`
- `GET /api/resources`, `GET /api/resources/{id}/download`
- `GET /api/leaderboard`
- Admin-only: `GET /api/admin/overview`, `GET /api/admin/leaderboard`, `GET/PATCH /api/admin/settings`, `POST /api/admin/resources`, `DELETE /api/admin/resources/{id}`, `POST /api/admin/students`, `DELETE /api/admin/students/{id}`, `POST /api/admin/students/{id}/status`, `GET/POST /api/admin/groups`, `POST /api/admin/groups/{id}/members`, `DELETE /api/admin/groups/{id}`, `DELETE /api/admin/groups/{id}/members/{studentId}`, `POST /api/admin/invites`, `GET /api/admin/delegates`
- Scoped helper tasks: `GET /api/admin/access`, `GET /api/admin/students`, `POST /api/admin/classes`, and grade-checked resource uploads, class management, and learner status changes

## Deployment notes

The included `http.server` server is intended for local development and private LAN demos, not direct public-internet hosting. Do not configure router port forwarding to expose it publicly. For a public deployment, place the app behind a maintained HTTPS reverse proxy and a production-grade WSGI server, set `COOKIE_SECURE=1`, use a high-entropy `ADMIN_SETUP_KEY` for the one-time setup, and restrict server/database backups appropriately. The admin role is checked by the API, not just hidden in the page.

## Vercel frontend

Vercel hosts the static frontend and rewrites `/api/*` directly to the Python backend. The backend and its SQLite database and uploads must remain on a host with persistent storage; Vercel's static deployment does not run `server.py` or provide durable local storage.

1. Deploy the Python backend using the **Free PythonAnywhere hosting** or **Render hosting** instructions below. Keep its database and uploads on persistent storage.
2. Import this repository as a Vercel project with the repository root as the project root and the **Other** framework preset. The included `vercel.mjs` config builds only the public frontend files into `dist` and routes `/admin` to the app entry page.
3. In the Vercel project's environment variables, set `LEARNLOOP_API_ORIGIN` for production and preview to the backend's HTTPS origin only, for example `https://yourusername.pythonanywhere.com` (no path or trailing slash). Redeploy after setting or changing it.
4. In the backend configuration, set `CORS_ALLOWED_ORIGINS` to the exact Vercel website origin before starting or reloading the backend. For example, in the PythonAnywhere WSGI configuration:

   ```python
   os.environ["CORS_ALLOWED_ORIGINS"] = "https://your-project.vercel.app"
   ```

   If you use a custom domain or Vercel preview deployments, include each exact `https://` site origin that needs to make API requests, separated by commas. Do not include a path or trailing slash.
5. Open the Vercel URL and create the admin account with the setup key configured on the backend. Once the admin account is created, remove the setup key from the backend configuration and reload or redeploy it.

The API rewrite keeps requests on the Vercel site origin and forwards them directly to the backend; the frontend does not contain the backend URL. The Python backend remains responsible for authentication, permissions, the database, and uploaded files. Vercel preview origins must also be allowed by the backend if you need to test sign-in or other write actions there.

## Cloudflare Pages frontend

Cloudflare Pages can also host the static website and proxy `/api/*` requests. It does not run this app's Python server or provide a persistent local SQLite database. Use the PythonAnywhere backend instructions below and follow the existing Pages setup:

1. Deploy the Python backend and retain its database and uploads in the persistent `learnloop-data` directory.
2. Set `CORS_ALLOWED_ORIGINS` in the PythonAnywhere WSGI configuration to the exact Cloudflare website origin (and any custom domain or preview origins), before importing `pythonanywhere_wsgi`.
3. Create a Cloudflare Pages project connected to the repository. Use the repository root, `python build_pages.py` as the build command, and `dist` as the build output directory.
4. Add `LEARNLOOP_API_ORIGIN` to the Pages project's production and preview environment variables, then redeploy.
5. Open the Pages URL and create the admin account with the backend setup key. Remove the setup key from the backend configuration after setup.

## Render hosting

The included `Dockerfile` and `render.yaml` configure a separate Render deployment with HTTPS cookies, a generated one-time admin setup key, and a persistent disk for the SQLite database and uploaded resources. The persistent disk requires a paid Render web service; do not remove it or deploy without it, or hosted data can be lost. This deployment starts with a new, empty database and does not change or copy the local `data` folder.

1. Push this project to a private GitHub repository, excluding `.env` and `data`. In Render, choose **New +** > **Blueprint** and connect that repository.
2. Review the Blueprint configuration. It creates a paid `starter` web service and a 1 GB persistent disk; confirm the current price and disk details in Render before applying it.
3. Wait for the service to become healthy. In the Render service's **Environment** settings, reveal the generated `ADMIN_SETUP_KEY`. Open the service's `https://...onrender.com` address, choose the admin setup option, and use that key to create an admin account. Do not share or commit the key.
4. After creating the admin account, remove `ADMIN_SETUP_KEY` from the service environment and redeploy. Then sign in at `https://...onrender.com/admin` with the admin email and password.

Keep the persistent disk attached and make backups. The hosted database is separate from local accounts and data; migrating existing records requires a separate, carefully handled backup/restore.

## Free PythonAnywhere hosting

PythonAnywhere's free web app uses WSGI rather than running `server.py` directly. The included `pythonanywhere_wsgi.py` adapts the existing app to WSGI and stores the SQLite database and uploaded files under your account's home directory so they are not tied to the project upload folder.

1. Create a free PythonAnywhere account and a **Manual configuration** web app using a supported Python version (Python 3.10 or newer).
2. Upload the project files (`server.py`, `pythonanywhere_wsgi.py`, `index.html`, `app.js`, `styles.css`, and `curriculum_content.json`) to `/home/<your-username>/learnloop`. Do not upload `.env`, `data`, or `__pycache__`.
   To keep existing local accounts and learning data, separately create `/home/<your-username>/learnloop-data/uploads` and copy `data\learnloop.sqlite3` and the contents of `data\uploads` into that persistent data folder. If you skip this, the hosted app starts with a new, empty database.
3. In the **Web** tab, open the WSGI configuration file. Set `your-username` to your PythonAnywhere username and use:

   ```python
   import os
   import sys

   project_home = "/home/your-username/learnloop"
   if project_home not in sys.path:
       sys.path.insert(0, project_home)

   os.environ["LEARNLOOP_DATA_DIR"] = "/home/your-username/learnloop-data"
   os.environ["COOKIE_SECURE"] = "1"
   os.environ["ADMIN_SETUP_KEY"] = "replace-with-a-long-random-one-time-key"

   from pythonanywhere_wsgi import application
   ```

   Generate a one-time setup key in a private console with `python -c "import secrets; print(secrets.token_urlsafe(32))"`. Keep it private and do not commit it. The WSGI configuration file is private to your hosting account.
4. Save the WSGI file and click **Reload** in the Web tab. Open the app's PythonAnywhere URL and choose **Set up / sign in as the local admin**. Enter the setup key from your WSGI configuration.
5. After setting up the admin account, remove the `ADMIN_SETUP_KEY` line from the WSGI file and reload the app. You can later reset the admin password in a PythonAnywhere console with:

   ```bash
   export LEARNLOOP_DATA_DIR="/home/your-username/learnloop-data"
   python /home/your-username/learnloop/reset_admin_password.py
   ```

The database and uploaded resources live in `/home/<your-username>/learnloop-data`; do not delete that folder when updating the app files. Download backups from PythonAnywhere's **Files** tab regularly. Free hosting has resource and availability limits, so treat this as a small demonstration deployment, not a guaranteed production service for sensitive student data. Protect learner information and use strong account passwords.
