# Egress scorecard — what each route can actually reach

Measured 2026-10-10 with `scripts/egress_scorecard.py`.

## The finding that matters

**TokenHarbor was never blocked.** The 429s we kept seeing were *self-inflicted*:
hammering `/login` (and `/api/me/free-tier`) in a tight loop is what provokes
Cloudflare's interstitial. Paced probes are clean.

The same is true of the Webshare reCAPTCHA — but there the block is real and
IP-keyed, and no amount of pacing fixes it.

## Results

| route | IP | ip-api flags | tokenharbor | cloudflare | webshare page | Google reCAPTCHA |
|---|---|---|---|---|---|---|
| **direct** | 182.8.255.126 | `hosting:true` | OK | OK | OK | **RECAPTCHA** |
| **WARP** | 104.28.219.241 | `proxy:true` | OK | OK | OK | **RECAPTCHA** |
| **TinyFish residential** | 13.57.x / 18.144.x (rotating) | clean | OK | OK | OK | **OK** |
| monosans free lists | ~1000 in pool | mixed | — | — | — | untested |

Free-list liveness (sampled): 9/40 HTTP alive, 5/80 mixed-scheme alive. Too
slow/unstable to drive a browser (60s page-load timeouts), even though they
work fine for plain HTTP.

## The two gates, separated

They are *different gates* and need different egress:

1. **Cloudflare interstitial** — triggered by *rate*, not identity. Paced
   requests pass from any IP, including ours. Fix: pace the probes.
2. **Google reCAPTCHA v2 audio** — keyed to *IP reputation*. `hosting:true`
   (our Telkomsel IP) and `proxy:true` (WARP's `104.28.x`, a well-known
   Cloudflare range) both get `"automated queries"`. Only a real residential
   or mobile IP clears it.

## Consequence for a new user

A new user has no TinyFish key and no residential proxy. So the only
self-serve path to a *reCAPTCHA-clearing* IP is:

- **mobile tether** (a real carrier IP — exactly the non-`hosting` egress that
  passes), which the repo already drives via `scripts/mobile_rotate.py` + adb,
  or
- **paid Webshare/residential**, or
- **the free lists**, which need a liveness filter and a browser timeout bump
  before they are usable at all.

`hosting:false` + `proxy:false` + `mobile:true` is the fingerprint to aim for.

## Bug fixed along the way

The first version of the classifier treated `/cdn-cgi/challenge-platform/...`
in the HTML as proof of an interstitial. That string is injected by **every**
Cloudflare site as the standard JS-detection bootstrap — so every healthy page
was misreported as CHALLENGE. A true interstitial is identified by
`"just a moment"`, `cf-mitigated`, `__cf_chl_`, or `"Enable JavaScript and
cookies to continue"` instead. This is why the scorecard now agrees with curl.
