import { createClaimClient } from "../../Auto-FreeCF-vendor-refs/zcode-api/src/claim/client.js";
import { getCaptchaToken, shutdownCaptcha } from "../../Auto-FreeCF-vendor-refs/zcode-api/src/proxy/captcha.js";
import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { createHash } from "node:crypto";

const TARGET_PLAN = "zcode-v3-start-plan-1106-wk";
const APP_VERSION = "3.17.1";
const LEDGER_PATH = "/home/amen/.config/auto-freecf/weekend_1106_claim_ledger.json";
const JWT_DIR = "/home/amen/.zcode-bridge/jwt";
const ACCOUNTS_FILE = "/home/amen/.zcode-bridge/accounts.txt";
const PROXIES_FILE = "/home/amen/Auto-FreeCF/signup_from_scratch/proxies.txt";

interface ClaimRecord {
  status: "claimed" | "failed" | "missing_jwt";
  plan?: string;
  startsAt?: number;
  endsAt?: number;
  note?: string;
  time: number;
}

function canonicalDeviceMid(email: string): string {
  const ns = Buffer.from("6ba7b8109dad11d180b400c04fd430c8", "hex");
  const hash = createHash("sha1").update(Buffer.concat([ns, Buffer.from(`zcode-device:${email.trim().toLowerCase()}`, "utf-8")])).digest();
  const b = Buffer.from(hash.subarray(0, 16));
  b[6] = (b[6] & 0x0f) | 0x50; // RFC 4122 v5
  b[8] = (b[8] & 0x3f) | 0x80; // variant
  const hex = b.toString("hex");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20, 32)}`;
}

async function main() {
  console.log(`=== ZCODE WEEKEND BUILD (1106-wk) RESIDENTIAL ROTATING CLAIMER ===`);
  console.log(`Target Plan: ${TARGET_PLAN} (500M Tokens / account)`);
  console.log(`App Version: ${APP_VERSION}`);

  const proxies = readFileSync(PROXIES_FILE, "utf-8")
    .split("\n")
    .map((l) => l.trim())
    .filter((l) => l && !l.startsWith("#"));

  console.log(`Loaded ${proxies.length} residential proxies.`);

  let ledger: Record<string, ClaimRecord> = {};
  if (existsSync(LEDGER_PATH)) {
    try {
      ledger = JSON.parse(readFileSync(LEDGER_PATH, "utf-8")) as Record<string, ClaimRecord>;
    } catch {
      ledger = {};
    }
  }

  const emails = readFileSync(ACCOUNTS_FILE, "utf-8")
    .split("\n")
    .map((line) => line.split(":")[0]?.trim().toLowerCase())
    .filter((email): email is string => Boolean(email));

  console.log(`Total accounts in pool: ${emails.length}`);
  const remaining = emails.filter((email) => ledger[email]?.status !== "claimed");
  console.log(`Accounts remaining to claim: ${remaining.length} (${emails.length - remaining.length} already claimed)`);

  let successCount = emails.length - remaining.length;
  let failCount = 0;

  for (let i = 0; i < remaining.length; i++) {
    const email = remaining[i];
    const jwtPath = join(JWT_DIR, `${email}.txt`);
    if (!existsSync(jwtPath)) {
      console.log(`[${i + 1}/${remaining.length}] ✗ ${email}: Missing JWT file`);
      ledger[email] = { status: "missing_jwt", time: Date.now() };
      writeFileSync(LEDGER_PATH, JSON.stringify(ledger, null, 2));
      continue;
    }

    const jwt = readFileSync(jwtPath, "utf-8").trim();
    const mid = canonicalDeviceMid(email);
    const proxyUrl = proxies[i % proxies.length];

    const proxyFetch = (url: string | URL | Request, init?: RequestInit): Promise<Response> => {
      return fetch(url, { ...init, proxy: proxyUrl });
    };

    const client = createClaimClient({
      origin: "https://zcode.z.ai",
      jwt,
      appVersion: APP_VERSION,
      platform: "linux-x64",
      deviceMid: mid,
      marketingDeviceMid: mid,
      fetchImpl: proxyFetch,
    });

    console.log(`[${i + 1}/${remaining.length}] Claiming for ${email} (via proxy ${i % proxies.length + 1})...`);

    let claimed = false;
    for (let attempt = 1; attempt <= 3; attempt++) {
      try {
        const captcha = await getCaptchaToken(APP_VERSION);
        if (!captcha) {
          console.log(`  attempt ${attempt}: failed to acquire captcha token`);
          await Bun.sleep(1500);
          continue;
        }

        const outcome = await client.claim(TARGET_PLAN, {
          verifyParam: captcha.verifyParam,
          region: captcha.region,
        });

        if (outcome.ok) {
          console.log(`  ✓ SUCCESS: Claimed 500M Tokens!`);
          ledger[email] = {
            status: "claimed",
            plan: TARGET_PLAN,
            startsAt: outcome.startsAt,
            endsAt: outcome.endsAt,
            time: Date.now(),
          };
          writeFileSync(LEDGER_PATH, JSON.stringify(ledger, null, 2));
          successCount++;
          claimed = true;
          break;
        }

        if (outcome.code === 1003 || outcome.failureKind === "already_claimed") {
          console.log(`  ✓ ALREADY CLAIMED: ${outcome.message}`);
          ledger[email] = {
            status: "claimed",
            plan: TARGET_PLAN,
            note: "already_claimed",
            time: Date.now(),
          };
          writeFileSync(LEDGER_PATH, JSON.stringify(ledger, null, 2));
          successCount++;
          claimed = true;
          break;
        }

        console.log(`  ✗ Attempt ${attempt} failed: ${outcome.failureKind} (${outcome.code}) - ${outcome.message}`);
        await Bun.sleep(outcome.code === 3007 ? 2000 : 1000);
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        console.log(`  ✗ Attempt ${attempt} exception: ${message}`);
        await Bun.sleep(1500);
      }
    }

    if (!claimed) {
      failCount++;
      ledger[email] = { status: "failed", time: Date.now() };
      writeFileSync(LEDGER_PATH, JSON.stringify(ledger, null, 2));
    }

    // Pacing between accounts
    await Bun.sleep(1800 + Math.random() * 400);
  }

  console.log(`\n=== MASS CLAIM FINISHED ===`);
  console.log(`Total Claimed: ${successCount}/${emails.length} accounts (${(successCount * 0.5).toFixed(1)} Billion Tokens)`);
  console.log(`Failed: ${failCount}`);

  shutdownCaptcha();
  process.exit(0);
}

main().catch(console.error);
