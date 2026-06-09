# Ganjier Replay Pipeline (WordPress plugin)

Upload this plugin to WordPress to get:

- **Admin dashboard** — Tools → Replay Pipeline (status table for every automation run)
- **REST API** — `POST /wp-json/gg/v1/pipeline-runs` (used by the Python pipeline)
- **Zoom webhook bridge** — `POST /wp-json/gg/v1/zoom-webhook` (replaces `zoom-webhook-bridge.php`)

## Install

1. Zip the `ganjier-replay-pipeline` folder (the zip root must contain `ganjier-replay-pipeline.php`).
2. WordPress Admin → Plugins → Add New → Upload Plugin.
3. Activate **Ganjier Replay Pipeline**.
4. Deactivate the old **Zoom Webhook Bridge** plugin if it is still active (this plugin supersedes it).

## Dashboard

After activation, open **Tools → Replay Pipeline** in wp-admin.

The table shows topic, recording date, Zoom account, status, YouTube link, replay post link, processed time, and errors.

## API

The Python pipeline logs runs with the same Application Password already used for replay posts:

```
POST https://ganjierguild.com/wp-json/gg/v1/pipeline-runs
Authorization: Basic <WP_USER:WP_APP_PASSWORD>
```

Runs with the same `recording_id` are updated in place (not duplicated).

## Zoom webhook URL

Point Zoom at:

```
https://ganjierguild.com/wp-json/gg/v1/zoom-webhook
```

The plugin forwards requests to `http://127.0.0.1:5055/zoom/webhook` where `pipeline.py --webhook` is running.
