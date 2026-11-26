# Deploy the dashboard to a VPS

This guide publishes the Streamlit dashboard on your domain over HTTPS. It serves a frozen data snapshot and is public by default. Run the scraper, import, and scoring pipeline on your development machine. The VPS only needs the dashboard and the snapshot, so it can operate without API keys or Quiver credentials and continue serving the snapshot if the pipeline fails.

## Prerequisites

- A VPS that can run a persistent Python process with WebSocket support. A server with two CPU cores and 4 GB of RAM is suitable. The dashboard uses about 800 MB of RAM per active session, and the Compose configuration caps its memory at 3 GB.
- A domain with an A record pointing to the VPS. You can register the domain with a different provider.

## Build the snapshot

On the development machine, run from the repository root:

```bash
source venv/bin/activate
python scripts/build_deploy_snapshot.py
```

The script writes the server data to `data/deploy/`.

| File | Approximate size | Contents |
| --- | --- | --- |
| `trades_snapshot.parquet` | 15 MB | Trades extracted from the 645 MB master dataset |
| `price_history.parquet` | 26 MB | Price history for the charts |
| `snapshot_manifest.json` | A few lines | Snapshot information displayed below the dashboard title |

The app first looks for the price cache in `data/cache/` and falls back to `data/deploy/` on the VPS. If neither cache exists, trade details still open and show that price history is unavailable. Git ignores `data/deploy/` to keep snapshots out of the public repository and its history.

## Prepare the server

Set up SSH key access before configuring the firewall. On an existing IONOS VPS, the original setup used **Server → Actions → Reinstall image** to set a root password and provide cloud-init user data under **Additional settings → Scripts → User data**. Reinstalling replaces the server image. Use this snippet when provisioning a fresh server and substitute your public key.

```yaml
#cloud-config
disable_root: false
runcmd:
  - mkdir -p /root/.ssh
  - chmod 700 /root/.ssh
  - echo 'ssh-ed25519 AAAA... your-comment' >> /root/.ssh/authorized_keys
  - chmod 600 /root/.ssh/authorized_keys
```

Allow TCP ports 80 and 443 in the provider firewall as well as on the server. In IONOS, open **Network → Firewall Policies**. You can remove the preset Plesk ports 8443 and 8447 if you do not use Plesk.

Run the following as root on Ubuntu:

```bash
apt update && apt upgrade -y
apt install -y ca-certificates curl rsync ufw fail2ban unattended-upgrades

# Install Docker from Docker's official Ubuntu repository
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
  https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
  > /etc/apt/sources.list.d/docker.list
apt update && apt install -y docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker

# Allow incoming SSH and web traffic
ufw default deny incoming && ufw default allow outgoing
ufw allow 22/tcp && ufw allow 80/tcp && ufw allow 443/tcp
ufw --force enable
```

On Ubuntu 24.04, SSH uses the first value it reads for each setting. Name the hardening file so it sorts before `50-cloud-init.conf`. Confirm key-based access before changing password authentication.

```bash
cat > /etc/ssh/sshd_config.d/00-hardening.conf <<'CONF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
PubkeyAuthentication yes
MaxAuthTries 3
CONF
sshd -t && systemctl reload ssh
sshd -T | grep -E '^(passwordauthentication|permitrootlogin) '   # Verify the effective settings
```

The provider's remote console remains available if you lose SSH key access.

## Copy the code and snapshot

Create the local deployment configuration from the template:

```bash
cp .deploy.env.example .deploy.env
chmod 600 .deploy.env
```

Set `DEPLOY_TARGET`, `DEPLOY_PATH`, and any values needed for end-to-end deployment tests. Git and the Docker build context exclude `.deploy.env`.

You can clone the public repository on the VPS or transfer the checkout from the development machine. The example uses a deploy account with access to the destination directory.

```bash
# Run from the repository root on the development machine
rsync -az --delete \
  --exclude='.git/' --exclude='.env' --exclude='.deploy.env' \
  --exclude='venv/' --exclude='archive/' \
  --exclude='__pycache__/' --exclude='data/' --exclude='logs/' \
  --exclude='deploy/.env' \
  ./ deploy@server.example:/srv/congress-tracker/

# Transfer the snapshot separately (about 41 MB)
rsync -az --mkpath data/deploy/ deploy@server.example:/srv/congress-tracker/data/deploy/
```

Excluding `data/` from the code transfer keeps the full trade master on the development machine. If you clone the repository on the server, run only the snapshot transfer.

## Set the domain and start the dashboard

On the server, create `$DEPLOY_PATH/deploy/.env`:

```dotenv
SITE_DOMAIN=example.com, www.example.com
# Optional dashboard password
APP_AUTH_ENABLED=0
APP_PASSWORD=
# Required legal notice and privacy policy details
LEGAL_NAME=
LEGAL_STREET=
LEGAL_CITY=
LEGAL_EMAIL=
LEGAL_PHONE=
```

List each hostname that Caddy should serve. Caddy requests a certificate for each one. Remove any parked AAAA record that directs IPv6 traffic to the domain provider's parking page.

```bash
cd /srv/congress-tracker
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env up -d --build
```

The first build takes a few minutes. Caddy provisions HTTPS automatically once DNS points to the VPS. Check the service status and logs:

```bash
docker compose -f deploy/docker-compose.prod.yml ps
docker compose -f deploy/docker-compose.prod.yml logs -f dashboard
```

## Publish a new snapshot

`scripts/import_trades.py` updates the trade master on the development machine. After the import finishes, run:

```bash
scripts/deploy_data.sh
```

This builds the snapshot and price cache, uploads `data/deploy/`, and recreates the dashboard container so Streamlit loads the new data. The script waits up to 180 seconds for the container to become healthy and exits with an error if it times out. Use `scripts/deploy_data.sh --dry-run` to print the commands. Override the destination with `DEPLOY_TARGET` and `DEPLOY_PATH`.

## Deploy code changes

A Git push does not automatically deploy the site. From the repository root, run:

```bash
scripts/deploy_code.sh
```

The script deploys the current commit (`HEAD`) and refuses to run with uncommitted changes. It preserves local secrets and runtime files, including `.env`, `.deploy.env`, `data/`, `logs/`, and the server's `deploy/.env`. It rebuilds the containers, waits for their health checks, and prints their status. Use `scripts/deploy_code.sh --dry-run` to preview the commands. For a release with new data and code, deploy the snapshot first, deploy the code second, then check the site in a browser.

Both deployment scripts require a destination path ending in `/congress-tracker` and check for the existing production Compose file and `deploy/.env` before uploading.

The opt-in rollback test at `tests/integration/test_deploy_e2e.sh` runs on the same VPS in a separate path, container, image, Compose project, and localhost port. It compares production state before and after the run, checks the manifest and Parquet file inside the test container, and uses Streamlit's health endpoint. Every data deployment must create a new container ID and reach a healthy state. The test refuses to proceed if it finds leftover `congress-tracker-e2e-*` paths, containers, images, networks, or volumes. Its exit handler cleans up test resources. If the client is killed with `SIGKILL` or the machine loses power, use `tests/integration/remote_cleanup_deploy_e2e.sh` with the reported run ID before retrying.

On September 2, 2026, the isolated VPS test deployed and rolled back a code change and a data change from 80 to 81 trades. Cleanup removed the test resources and both production services stayed healthy.

## Require a dashboard password

The default setup lets visitors view the dashboard without signing in. To restrict access, set these values in `$DEPLOY_PATH/deploy/.env`:

```dotenv
APP_AUTH_ENABLED=1
APP_PASSWORD=<your-password>
```

Then run `docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env up -d`. Keep the password in the deployment environment. After five failed attempts, the form locks for the current session. The limit is configurable as `APP_AUTH_MAX_ATTEMPTS` in `config/settings.py`.

## Legal notice and privacy policy

Caddy serves `/impressum` and `/datenschutz` from the templates in `deploy/legal/`. The pages remain available during dashboard restarts and when password protection is enabled. Visitors can reach both pages from the dashboard sidebar, and neither page requires JavaScript.

Set `LEGAL_NAME`, `LEGAL_STREET` (including house number), `LEGAL_CITY` (including postal code), `LEGAL_EMAIL`, and `LEGAL_PHONE` in the server's `deploy/.env`. Missing values appear as red `[Angabe fehlt: ...]` placeholders.

The privacy policy documents truncated IP addresses, access logs without cookie or session headers, 14-day retention with rotation at 00:00 UTC, and an error log without visitor data. It also describes the `_streamlit_xsrf` cookie and the absence of third-party services. Streamlit usage telemetry is disabled (`gatherUsageStats = false`). Update `deploy/legal/datenschutz.html` when changing Caddy logging, `.streamlit/config.toml`, or third-party services such as maps, fonts, or analytics.

After entering the legal details, run `scripts/deploy_code.sh`. When migrating from the older configuration that logged full IP addresses, Compose recreates Caddy for the changed environment and volumes, which also removes the old Docker log. Remove the old access logs and restart Caddy to open a fresh log:

```bash
docker exec congress-caddy sh -c 'rm -f /var/log/caddy/access*'
docker restart congress-caddy
docker exec congress-caddy ls -la /var/log/caddy   # Verify that only the new access.log remains
```

Check `/impressum` and `/datenschutz` on your domain for missing fields. If a later deploy changes only the mounted Caddyfile, run `docker restart congress-caddy` to load it.

## What the VPS runs

The dashboard reads the snapshot without API keys, Quiver credentials, or outbound data requests. The dashboard image excludes the scraper, import tools, Tesseract, and Chromium. The container runs as an unprivileged user with read-only snapshot and price cache mounts. Public mode hides the Recent tab's CSV export to prevent visitors from writing files to the server.
