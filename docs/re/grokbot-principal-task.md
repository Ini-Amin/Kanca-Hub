# Task: find the exact principal/accountScope derivation in Grok Bot

Bundle: `main-core.cjs` and `main-app.cjs` (minified, 2.6 MB).
Runtime exports observed:

    { get accountScopeOfPrincipal(){ return Ag },
      get accountScopeOfToken(){ return Bn },
      get parseJwtPayload(){ return Vi },
      get tokenSubjectScope(){ return cU },
      get shouldRefreshAccessToken(){ return pU },
      get usesDevAuthFlow(){ return wA },
      get authClientIdOf(){ return Rg } }

`Ag` and `Bn` are minified bindings defined somewhere in the same bundle.
Plain `grep -E "Ag ?="` finds nothing, so they are likely declared as
`function Ag(...)`, `Ag = (...) =>`, or inside a destructuring/export map.

## What is needed

1. The body of `Ag` (accountScopeOfPrincipal) and `Bn` (accountScopeOfToken).
2. The body of `Vi` (parseJwtPayload), `cU` (tokenSubjectScope), `wA` (usesDevAuthFlow).
3. Whether `usesDevAuthFlow` lets us bypass a signed principal entirely.
4. What `sand-secrets.json` keys the app actually reads to build a principal
   (candidate names: cursor-access-token, cursor-refresh-token,
   cursor-account-profile, cursor-accounts).

## Observed facts (do not re-derive)

- `sand-secrets.json` currently holds 28-byte blobs prefixed `djEw` (base64 "v10").
  These are NOT JWTs and are not signed principals.
- The app calls `DashboardService/GetMe` exactly twice per launch, then stops.
- `connection.transport` stays `"down"`, `roster.agentsById.count == 0`.
- `AnalyticsService/BootstrapStatsig` is called ~1400x/minute forever.
- The shim answers every aiserver.v1 RPC, so it is not a 404/retry issue.

## Deliverable

Exact code, quoted from the bundle, for `Ag`, `Bn`, `Vi`, `cU`, `wA`, plus a
concrete statement of what value would satisfy them (JWT shape? signature? a
plain account id?). If `usesDevAuthFlow` is a bypass, say exactly which env var
or secrets key flips it.
