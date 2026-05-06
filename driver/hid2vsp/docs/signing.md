# hid2vsp signing strategy

## Current (v0.1 dev / Phase 1-4)

**Self-signed test cert** + Windows test-signing mode enabled on each
target machine. Acceptable for:

- Dedicated industrial test PCs that the customer admins (one-time
  setup with `bcdedit /set testsigning on`)
- Internal dev machines (Rosen's VM)
- CI test runners

**Not acceptable for:** general consumer Windows, locked-down corporate
endpoints, shared POS terminals.

## Path to production-signed (deferred)

When there's a paying customer or business case that requires
production deployment to non-test-mode machines, choose ONE:

### Option A — WHQL via Microsoft Partner Center (free signing)

- Register a business account at https://partner.microsoft.com — needs
  a verified company name + tax ID
- One-time EV cert ~$99 if no Microsoft Pay / verified-business chain
- Submit driver package → Microsoft signs it after passing HLK tests
- Lead time: 1-3 weeks for first submission, days for updates
- Resulting catalog signs with MS Cross-Cert + MS Hardware Lab → loads
  on ANY Win 10/11 with HVCI on, no test mode needed

### Option B — Buy a standard EV code-signing cert ($200-300/yr)

- DigiCert / Sectigo / GlobalSign — pick one
- Faster to get (days vs weeks)
- Sign locally with `signtool sign /v /tr http://timestamp.digicert.com /td sha256 /fd sha256 hid2vsp.cat`
- Driver loads on Win 10/11 without test mode IF user manually accepts
  in Device Manager OR the cert chain is recognized
- For HVCI = on, **EV cert PLUS Attestation Signing** through Partner
  Center is needed — so option B alone doesn't fully replace option A
  for HVCI machines

### Recommendation

**Option A (WHQL)** is the right long-term choice for a free / open-
source distribution model — zero per-year cost after initial $99 EV
cert (or $0 if Rosen's existing business chain is verified by MS).
Lead time is acceptable since driver releases will be infrequent (3-5
per year max).

Defer this until there's a concrete commercial customer requesting
production deployment without test mode.

## What to NOT do

- ❌ Use third-party patched binaries (com0com.com FuJian Newland
      build, etc.) — signing chain is opaque, EULA unclear, can't be
      bundled in our installer cleanly
- ❌ Distribute unsigned binaries — won't load on any modern Windows
- ❌ Buy individual cert per developer — costs add up, doesn't help
      production deploy story

## Test-mode UX in our installer

When the installer detects:

- Test mode OFF + no production cert available → show warning,
  document the `bcdedit /set testsigning on` + reboot procedure,
  refuse to install driver section (offer HTTP-only mode instead).
- Test mode ON → install driver normally.
- Production-signed catalog present → install driver normally even
  without test mode.

This keeps the installer honest about what it can deliver and pushes
users away from misconfigured deployments.
