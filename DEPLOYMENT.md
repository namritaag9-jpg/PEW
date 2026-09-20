# Patel POS V2.4 – Free Hosting Deployment

## Important
This package is configured to bind to `0.0.0.0` and use the hosting provider's `PORT`.
The detected Flask entrypoint is `pos/app.py` and the Gunicorn target is `pos.app:app`.

## Render
1. Push this folder to a GitHub repository.
2. In Render, create **New > Blueprint** or **New > Web Service**.
3. Select the repository.
4. Build command: `pip install -r requirements.txt`
5. Start command: `gunicorn --bind 0.0.0.0:$PORT pos.app:app`
6. Deploy.

## Railway
1. Push this folder to GitHub.
2. Create a new Railway project from the repository.
3. Railway will use `railway.json`/Procfile.
4. The app must use `$PORT` automatically.

## PythonAnywhere
1. Upload/extract the project into your PythonAnywhere home directory.
2. Create a virtualenv and install:
   `pip install -r requirements.txt`
3. Create a Python web app.
4. Set the WSGI file to the included `pythonanywhere/wsgi.py` content/path.
5. Update `project_home` if your upload directory differs.
6. Reload the web app.

## Data persistence warning
Free hosting services may have temporary or restricted disks. For production use, move customer/job data to a managed database and configure backups.
