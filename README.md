<div align="center">

**English** · [Français](README.fr.md)

<img src="docs/media/banner.svg" alt="vinted-sniper — get told the moment something matches" width="620">

[![CI](https://github.com/jasp-nerd/vinted-sniper/actions/workflows/ci.yml/badge.svg)](https://github.com/jasp-nerd/vinted-sniper/actions/workflows/ci.yml)
[![Container image](https://img.shields.io/badge/ghcr.io-vinted--sniper-2496ED?logo=docker&logoColor=white)](https://github.com/jasp-nerd/vinted-sniper/pkgs/container/vinted-sniper)
[![Python 3.13+](https://img.shields.io/badge/python-3.13+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Watch a Vinted search and get a message when something new matches —<br>
in Telegram, Discord, or wherever else you want it.

</div>

You paste the URL of a search you already made on Vinted. It checks that search every minute
or so and tells you about listings that weren't there before. That's the whole idea.

<div align="center">
  <img src="docs/media/demo.gif" width="820" alt="Adding a search by pasting a Vinted URL, the saved searches with their health, and the listings arriving in Discord and Telegram">
</div>

### The same listing, as it arrives in each

<table>
  <tr>
    <td width="50%" valign="top" align="center">
      <img src="docs/media/discord.png" width="100%" alt="A Discord notification showing the title, the price with buyer protection included, size, brand, condition, seller and photo, with buttons to open the listing, message the seller, or buy">
      <br><sub><b>Discord</b> — one message per listing, batched into embeds when several land at once</sub>
    </td>
    <td width="50%" valign="top" align="center">
      <img src="docs/media/telegram.png" width="100%" alt="The same kind of notification in Telegram: photo preview, price with buyer protection included, brand, size, condition, seller, and the same three buttons">
      <br><sub><b>Telegram</b> — photo as a preview, so the message keeps its buttons</sub>
    </td>
  </tr>
</table>

## What it does that others don't

**It filters on what you actually pay.** Vinted's own price filter uses the asking price, so
a search capped at €30 will happily show you something that costs €33 once buyer protection
is added. Set a maximum here and it applies to the total.

**It tells you when it has stopped working.** Vinted occasionally keeps answering normally
while quietly serving a catalog that never updates. From the outside that looks exactly like
a quiet night. This watches for a search going silent while your other searches on the same
site keep finding things, and when that happens it starts a fresh session and says so out
loud. Every search shows its last successful check, its last error, and whether it looks
stuck.

**It doesn't touch your account.** No login, no password, no cookies from your browser. It
reads public listings the way a logged-out visitor does. Vinted has been restricting accounts
it suspects of automation — there is no account here for that to happen to. It also means it
cannot buy anything for you, which is deliberate.

**It stays quiet on the first run.** A new search records what's already there without
notifying you about ninety-six things you didn't ask about.

## Quickstart

You need Docker. Three commands:

```bash
curl -O https://raw.githubusercontent.com/jasp-nerd/vinted-sniper/main/docker-compose.yml
curl -o .env https://raw.githubusercontent.com/jasp-nerd/vinted-sniper/main/.env.example
docker compose up -d
```

On Windows PowerShell, `curl` is an alias for something else, so use this instead:

```powershell
curl.exe -O https://raw.githubusercontent.com/jasp-nerd/vinted-sniper/main/docker-compose.yml
curl.exe -o .env https://raw.githubusercontent.com/jasp-nerd/vinted-sniper/main/.env.example
docker compose up -d
```

Before starting, open `.env` and set at least one way to reach you. The shortest path is a
Discord webhook: in your server, Settings → Integrations → Webhooks → New Webhook → Copy URL.

Then add it and a search:

```bash
docker compose exec vinted-sniper vinted-sniper destination discord "https://discord.com/api/webhooks/..."
docker compose exec vinted-sniper vinted-sniper watch "https://www.vinted.fr/catalog?search_text=nike+air+max&price_to=40"
```

To use the dashboard instead of the command line, set `VINTED_SNIPER_WEB_ENABLED=true` and
`VINTED_SNIPER_WEB_AUTH_TOKEN` in `.env` (generate one with `openssl rand -hex 32`), then open
http://localhost:8000.

Before setting anything up, you can check that Vinted is reachable from your machine:

```bash
docker compose exec vinted-sniper vinted-sniper check --url "https://www.vinted.fr/catalog?search_text=nike"
```

That does one real request and prints what came back.

## Getting the search URL

Open Vinted, search for what you want, and set the filters — category, size, brand, price,
condition. Once results are showing, copy the address bar. That URL is the input.

Any country site works: `vinted.fr`, `.de`, `.nl`, `.co.uk`, `.com`, and the rest. The site
you copied from is the site it watches, and the links you get back point there too.

Tracking parameters are stripped, so pasting the same search twice is recognised as the same
search rather than doubling your requests.

## Where notifications go

| Channel | Setup |
|---|---|
| Discord | Paste a webhook URL. Nothing to invite, nothing to host. |
| Telegram | Create a bot with [@BotFather](https://t.me/BotFather), then run `vinted-sniper pair-telegram` and tap the link it prints. It finds your chat id for you. |
| ntfy | Pick a topic name, install the app. No account. |
| Anything else | A plain JSON POST to a URL you choose — n8n, Home Assistant, a script. |

Each search can go to its own set of destinations, so a Discord channel for one thing and
your phone for another is normal.

There is also an RSS feed per search if you'd rather pull than be pushed.

## How fast is it, really

Checks default to once a minute per search and won't go below ten seconds. That floor isn't
caution for its own sake: Vinted's own API lags behind what people upload, sometimes by
minutes, occasionally by much longer. Checking every two seconds finds nothing sooner and
does get you blocked.

Anyone promising you zero-delay Vinted alerts is selling something. Each notification shows
both when Vinted says the listing appeared and when we found it, so you can see the
difference yourself.

## What breaks, and how often

Vinted has no public API and no obligation to keep the private one working. Three things go
wrong in practice:

**403, blocked.** Usually the address you're connecting from rather than anything you did.
Home connections are rarely affected; some cheap VPS ranges are. It backs off, starts a new
session, and keeps going. If it persists, [the troubleshooting
guide](docs/troubleshooting.md) has a one-line test that tells you whether it's your IP or
the app.

**The catalog freezes.** Covered above. The watchdog handles this.

**Vinted changes something.** It happens a few times a year. A scheduled job runs one real
request a week against the live site so breakage shows up here before it shows up for you.

## Running it somewhere

A machine at home is the best place for this: a Raspberry Pi, an old laptop, a NAS. It costs
nothing, and home connections get challenged far less than datacenter ones. A €5 VPS also
works fine. [The self-hosting guide](docs/self-hosting.md) covers both, and explains which
free tiers to avoid and why.

There is no hosted version, and that's on purpose. One server making everyone's requests
would concentrate exactly the risk that makes this work well when it's spread across many
ordinary connections.

## Configuration

Everything is environment variables, documented in [`.env.example`](.env.example) and
[docs/configuration.md](docs/configuration.md). Your searches and destinations live in the
database, managed through the dashboard or the CLI — there's no config file to keep in sync.

Useful commands:

```
vinted-sniper watch <url>       add a search
vinted-sniper searches          list them
vinted-sniper status            how each one is doing
vinted-sniper check --url <url> one-off test fetch
vinted-sniper destination ...   add somewhere to send
```

## Development

```bash
uv sync --all-extras
uv run pytest
uv run ruff check . && uv run mypy
```

`VINTED_SNIPER_FETCH_MODE=mock` replays recorded responses from disk, so you can work on it
without touching Vinted at all. There's more in [docs/architecture.md](docs/architecture.md),
and the standards changes are held to are in [docs/REVIEW.md](docs/REVIEW.md).

## A note on the rules

This isn't affiliated with Vinted. It reads public listing pages anonymously, at deliberately
conservative rates, and stores what it finds on your own machine. Vinted's terms prohibit
automated access; running this means accepting that risk yourself. It does not log into your
account, does not buy or list anything, and does not build profiles of sellers.
[docs/legal.md](docs/legal.md) says more, including what to keep in mind about other people's
data.

MIT licensed. Contributions welcome — especially bug reports with logs attached.
