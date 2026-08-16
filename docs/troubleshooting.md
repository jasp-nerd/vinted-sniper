# When it stops finding things

Start here: `vinted-sniper status`. It prints every search with a one-word state, when it
last succeeded, and what went wrong if anything did.

```
Running.

nike air max [ok] — vinted.fr
  last successful check: 34s ago
  newest listing seen:   112s ago

carhartt jacket [failing] — vinted.co.uk
  last successful check: 1840s ago
  blocked 12 times, rate limited 0 times
  last error: vinted.co.uk refused the request with 403
```

The state tells you which of the following sections to read.

## "ok" but nothing is arriving

The search is working; it just isn't matching anything. Common causes, in the order worth
checking:

**Your price limit includes buyer protection.** A limit of 20 rejects a listing priced at 18
that costs 20.50 to actually buy. That is the intended behaviour, but it surprises people.
Check what you set with `vinted-sniper searches`.

**The search itself is narrow.** Open the URL in a browser. If Vinted shows nothing new
either, there is nothing wrong.

**Excluded words are catching more than you meant.** Excluding "new" also excludes "brand new
condition" and anything else containing it.

**Everything is a boosted listing.** Paid bumps are skipped, because they are old listings
resurfacing rather than new ones.

## "stale"

The search has stopped seeing new listings while your other searches on the same site are
still finding them. Vinted sometimes serves a catalog that has quietly stopped updating,
which looks like success from the outside.

The app starts a fresh session on its own when it notices. If it stays stale for more than
an hour, restart it:

```bash
docker compose restart vinted-sniper
```

If it persists after that, the search may have genuinely dried up. Compare with the same URL
in a browser.

## "failing" with 403

Vinted is refusing the connection. Almost always this is about where the request comes from,
not what it asked for. Confirm it in one command:

```bash
curl -v -c - -L "https://www.vinted.fr/" 2>&1 | grep access_token_web
```

Use whichever country site you are watching.

**If that prints a cookie line**, your address is fine and the problem is the app — please
open an issue with your logs.

**If it prints nothing**, your address is being challenged, and no setting will fix that.
Options, cheapest first:

1. **Wait.** These are usually temporary. The app backs off on its own.
2. **Slow down.** Raise `VINTED_SNIPER_POLL_DEFAULT_INTERVAL_S` to 120 or 300. Ten searches
   at once from one address is a lot more traffic than one search.
3. **Move it home.** Residential connections are challenged far less than datacenter ones. A
   Raspberry Pi is enough.
4. **Turn on TLS impersonation.** Set `VINTED_SNIPER_HTTP_IMPERSONATE=true`. This makes
   requests look like a real browser at the connection level rather than like Python. It
   needs the `impersonate` extra installed, and it is not a magic fix — if your address is
   blocked outright, it stays blocked.
5. **Use a proxy.** `VINTED_SNIPER_PROXY_FILE` points at a text file with one proxy URL per
   line. Free proxy lists are already blocked; if you go this route, use residential proxies
   in the same country as the site you are watching. Most people never need this.

## "failing" with 429

You are checking too often for how Vinted feels about your address right now. The app already
waits as long as Vinted asks it to. If it keeps happening, increase the interval — the
listings will still be there.

## "failing" with malformed

Vinted answered with something that is not a catalog. That usually means either an anti-bot
interstitial (see the 403 section) or a change to their API, which needs a fix here. Worth
opening an issue with the logged error.

## Notifications stopped but searches are fine

Check the destination:

```bash
vinted-sniper destinations
```

A destination marked `disabled` was switched off because the other end said it was gone — a
deleted Discord webhook, a blocked Telegram bot, a wrong chat id. This is deliberate:
repeatedly sending to a dead webhook is what gets your address rate-limited by Discord
itself. Remove it and add the new one.

Also worth knowing: undelivered notifications are discarded after an hour by default. If
Discord was down all night, you get the current listings when it comes back rather than a
flood of stale ones.

## It says "Not running"

The heartbeat is stale, meaning the process is not coming round its loop.

```bash
docker compose logs --tail 50 vinted-sniper
docker compose ps
```

If the container is restarting repeatedly, the logs will say why. The most common cause is
`VINTED_SNIPER_WEB_ENABLED=true` without a `VINTED_SNIPER_WEB_AUTH_TOKEN`, which is refused
on purpose.

## Telegram never connects

The bot cannot message you first — that is a Telegram rule, not a limitation here. Run
`vinted-sniper pair-telegram --bot-username yourbot`, then tap the link it prints, in the chat
or group you want alerts in. The app must be running for the link to work, and the link
expires after thirty minutes.

For a group, add the bot to the group first. For a forum topic, tap the link inside that
topic.

## Everything is broken after an update

Roll back to the previous image tag and open an issue:

```yaml
image: ghcr.io/jasp-nerd/vinted-sniper:0.1.0
```

The weekly canary tests against the live site, so an outright break usually gets caught
before it ships. If you hit one anyway, the logs from `docker compose logs` are the useful
thing to attach.
