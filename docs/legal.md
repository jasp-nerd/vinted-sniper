# Rules, risks, and other people's data

Not legal advice. This is what the project assumes and why, so you can decide for yourself.

## Vinted's terms

Vinted's terms of service prohibit automated access — bots, scrapers, crawlers, the usual
list. That is a contract between you and Vinted, not a law. Breaking it is not a crime; it
gives Vinted grounds to stop serving you.

In the EU, reading publicly available pages is not in itself unlawful. Courts in several
jurisdictions have reached similar conclusions about public data. What the terms give Vinted
is the right to block you and, if you have an account, to close it.

## What that means in practice

This tool reads public listing pages anonymously. It never logs in, never sends your
credentials anywhere, and never touches your Vinted account. There is no account attached for
Vinted to restrict.

The realistic worst case is that your IP address stops being served, which is temporary and
reversible. That is a meaningfully different risk from tools that log in to buy automatically:
Vinted has been restricting accounts it suspects of automation, and an account restriction
costs you your purchase history, your listings and your reputation.

This is also why buying is not a feature here and will not become one.

## Being a reasonable guest

The defaults are deliberately unhurried, and worth keeping that way:

- One check a minute per search, with a floor of ten seconds. Vinted's own catalog lags behind
  uploads anyway, so faster finds nothing sooner.
- One request per check. No walking through pages, no fetching every listing's detail page.
- Sessions are reused rather than re-established constantly.
- Backing off when told to, and honouring the wait Vinted asks for.

If you run many searches, raise the interval rather than adding parallelism. The point is to
be a slightly unusual visitor, not a load test.

## Other people's data

Listings contain personal data: seller usernames, profile links, sometimes a city, sometimes a
rating. Under the GDPR that makes you a controller of that data once you store it, even for
personal use. The personal-use exemption is narrower than people assume, and it does not
survive publishing or sharing.

How the project keeps that small:

- Only what a notification needs is stored: username and rating, not full profiles.
- Full API payloads are off by default (`KEEP_RAW_JSON=false`).
- Listings are deleted after thirty days.
- Everything stays in a file on your machine. Nothing is sent anywhere except the
  notifications you configured.
- No seller profiling, no cross-referencing, no aggregate datasets.

If you change these — turning on raw payloads, extending retention, exporting the database —
you are taking on more responsibility for that data. If you make anything public, you need a
lawful basis, and "it was already public" is not one.

Do not use this to build a dataset about individual sellers, to resell scraped data, or to
target anyone. That is a different activity from watching for a jacket in your size.

## Sharing what you find

Notifications are for you. Republishing listing photos or descriptions at scale runs into
copyright — the seller took those photos — as well as the concerns above. A link is fine. A
mirror is not.

## If Vinted asks you to stop

Then stop. This project has no interest in being adversarial with them; it exists because the
official notifications are slow, not because anyone wants a fight. If you receive a notice,
comply with it, and please open an issue so other people know.
