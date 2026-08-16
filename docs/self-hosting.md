# Running it somewhere

Two things decide where to put this: it has to stay on, and the connection it makes requests
from matters more than the machine's speed. A Raspberry Pi on your home internet outperforms
a fast server on a blocked address.

## At home (recommended)

Any always-on machine works: a Raspberry Pi 4 or 5, an old laptop, a NAS, a desktop that
doesn't sleep. The app idles at a few percent of one core and a few dozen megabytes.

Why home first:

- Home connections are challenged far less often than datacenter ones by anti-bot systems.
- Nothing to pay, and no free tier that can be withdrawn.
- The database, your webhook URLs and your Telegram token stay on hardware you own.

### On a Raspberry Pi or any Linux box

Install Docker if you don't have it:

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER   # log out and back in afterwards
```

Then:

```bash
mkdir -p ~/vinted-sniper && cd ~/vinted-sniper
curl -O https://raw.githubusercontent.com/jasp/vinted-sniper/main/docker-compose.yml
curl -o .env https://raw.githubusercontent.com/jasp/vinted-sniper/main/.env.example
nano .env          # set a web token if you want the dashboard
docker compose up -d
```

Images are published for both x86 and ARM, so the same commands work on a Pi.

Check it started:

```bash
docker compose logs -f vinted-sniper
```

`restart: unless-stopped` is already in the compose file, so it comes back after a reboot or
a power cut.

### On a NAS

Synology, Unraid, CasaOS and Umbrel can all run a compose file. In Portainer, use Stacks →
Add stack → Repository, pointing at this repo. The only setting that needs attention is the
volume: keep `/data` on persistent storage, not a scratch disk.

### On Windows or macOS

Docker Desktop works, but neither machine is likely to stay awake and online reliably.
Disable sleep first, or use something that is always on.

## On a VPS

Reasonable if you have no machine at home. Around €5 a month:

| Provider | Notes |
|---|---|
| Hetzner CX22 | Cheapest sensible option. Their address ranges have a mixed reputation with anti-bot systems, so test before committing. |
| DigitalOcean | Costs a little more, easiest control panel if this is your first server. |
| Contabo | Cheap, generous specs, variable performance. |

Setup is the same as above. Before you commit, run the connection test from the VPS:

```bash
curl -v -c - -L "https://www.vinted.fr/" 2>&1 | grep access_token_web
```

No cookie printed means that address is already being challenged, and you should pick a
different provider rather than fight it.

If you enable the dashboard on a VPS, do not expose port 8000 to the internet directly. Keep
the loopback binding in the compose file and reach it through an SSH tunnel:

```bash
ssh -L 8000:localhost:8000 you@your-server
```

Then open http://localhost:8000 on your own machine. Anything else needs a reverse proxy with
TLS in front, because the dashboard can see your webhook URLs.

## Free tiers worth avoiding

**Oracle Cloud Always Free.** Their reclamation policy deletes instances that stay idle, and
a polling bot idles at a few percent CPU by design. They also cut the free ARM allowance in
2026 with little notice. Your instance will eventually disappear.

**Render's free tier.** Free web services sleep after fifteen minutes without traffic, and
background workers are not included in the free plan. This is a background worker.

**Railway and Fly.io.** Both dropped their free tiers. They work fine as paid hosts, at
roughly VPS prices, but don't plan around them being free.

## Backups

Everything lives in one SQLite file inside the `vinted-sniper-data` volume. To copy it out:

```bash
docker compose cp vinted-sniper:/data/app.db ./app-backup.db
```

Losing it costs you your searches and destinations, not much else — listings are pruned after
thirty days anyway.

## Updating

```bash
docker compose pull && docker compose up -d
```

Schema changes are applied automatically at startup. If an update misbehaves, pin the previous
tag in `docker-compose.yml` and open an issue.

## Running without Docker

```bash
git clone https://github.com/jasp/vinted-sniper && cd vinted-sniper
uv sync --extra web
cp .env.example .env
uv run vinted-sniper run
```

For a systemd service, point `ExecStart` at `/path/to/.venv/bin/vinted-sniper run`, set
`WorkingDirectory`, and add `Restart=always`. Note that the app handles SIGTERM itself, so
the default `KillSignal` is correct and there is no need for a long `TimeoutStopSec`.
