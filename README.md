# Mansoura Uni Leaked Database

A private, access-controlled Flask application for browsing an internal directory dataset. The project is designed so the application code can live in GitHub while sensitive data and local access records stay out of the repository.

![Logo](Assets/readme.png)

## Purpose

This app provides a protected search interface for approved users only. Access is managed by an administrator, and users can be assigned limited preview access or full access.

## Access Levels

- **Pending**: User has requested access and is waiting for admin approval.
- **Preview**: User can view a limited subset of records.
- **Full**: User can view the full approved dataset.
- **Admin**: User can approve, contact, or delete access requests.

## Features

- Login-protected database browsing.
- Access request form for new users.
- Admin approval dashboard.
- Preview and full-access roles.
- Search by available record fields.
- Paginated results.
- Local email notification support for new access requests.

## Private Files

The real data files and local access records are intentionally not committed to GitHub. Keep these files private and provide them only through a secure deployment process or private storage service.

Ignored by default:

```gitignore
Data/*.json
.env
.env.*
```

Use `.env.example` as a safe template for required environment variables.

## Local Setup

Install dependencies:

```bash
pip install flask werkzeug
```

Run the app from the project root:

```bash
python App/app.py
```

Open locally:

```text
http://127.0.0.1:5000
```

## Email Notifications

To receive access request notifications, create a private `.env.local` file from `.env.example` and fill in SMTP settings. Do not commit real SMTP credentials.

Required values:

```env
SECRET_KEY=
APP_BASE_URL=
SMTP_HOST=
SMTP_PORT=587
SMTP_USERNAME=
SMTP_PASSWORD=
SMTP_FROM=
SMTP_USE_SSL=false
```

For mail providers that require app passwords, use an app password instead of your normal account password.

## Vercel Admin Login

For hosted deployments, create an admin account through environment variables instead of committing credentials or relying on local JSON storage.

Set these privately in Vercel Project Settings:

```env
ADMIN_EMAIL=
ADMIN_PASSWORD=
ADMIN_NAME=Admin
```

Use a strong unique password. Do not commit real admin credentials to GitHub.

## Deployment Notes

For public hosting, do not rely on writable local JSON files for approvals or data. Use a managed private database or storage service for production.

Recommended production structure:

- GitHub: application code only.
- Vercel: deployment and environment variables.
- Private database/storage: sensitive records and access roles.
- Environment variables: secrets and mail credentials.

## Security Notes

- Do not commit real data.
- Do not commit `.env` or `.env.local`.
- Rotate credentials if they are accidentally exposed.
- Keep the repository private until production storage and access controls are fully configured.
- Review access requests before assigning preview or full access.

## Status

Private internal tool. Access is by approval only.
