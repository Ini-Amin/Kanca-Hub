import { runCaptchaSolve } from "../../Auto-FreeCF-vendor-refs/zcode-api/src/proxy/captcha-solver.js";
import { ProxyAgent, setGlobalDispatcher } from "undici";

const DEFAULT_SCENE = "11xygtvd";
const DEFAULT_PREFIX = "no8xfe";
const DEFAULT_REGION = "sgp";

interface SolveArgs {
  scene: string;
  region: string;
  prefix: string;
  proxy?: string;
}

export async function solveCaptcha(args: SolveArgs): Promise<{ ok: boolean; verifyParam?: string; region: string; durationMs: number; error?: string }> {
  const t0 = performance.now();
  if (args.proxy) {
    setGlobalDispatcher(new ProxyAgent(args.proxy));
  }

  try {
    const param = await runCaptchaSolve(args.scene, args.region, args.prefix);
    const dt = Math.round(performance.now() - t0);
    return {
      ok: true,
      verifyParam: param,
      region: args.region,
      durationMs: dt,
    };
  } catch (err) {
    const dt = Math.round(performance.now() - t0);
    const message = err instanceof Error ? err.message : String(err);
    return {
      ok: false,
      region: args.region,
      durationMs: dt,
      error: message,
    };
  }
}

async function main() {
  const argv = process.argv.slice(2);

  if (argv[0] === "serve") {
    const port = Number(argv[1] || process.env.ALIYUN_SOLVER_PORT || 8783);
    console.log(`[happy-solver] Starting Aliyun Captcha HTTP daemon on port ${port}...`);

    Bun.serve({
      port,
      async fetch(req) {
        if (req.method === "GET" && new URL(req.url).pathname === "/health") {
          return new Response(JSON.stringify({ ok: true, solver: "happy-dom", runtime: "bun" }), {
            headers: { "Content-Type": "application/json" },
          });
        }

        if (req.method === "POST" && new URL(req.url).pathname === "/solve") {
          try {
            const body = (await req.json()) as Partial<SolveArgs>;
            const scene = body.scene || DEFAULT_SCENE;
            const region = body.region || DEFAULT_REGION;
            const prefix = body.prefix || DEFAULT_PREFIX;
            const proxy = body.proxy;

            const res = await solveCaptcha({ scene, region, prefix, proxy });
            return new Response(JSON.stringify(res), {
              status: res.ok ? 200 : 500,
              headers: { "Content-Type": "application/json" },
            });
          } catch (e) {
            const message = e instanceof Error ? e.message : String(e);
            return new Response(JSON.stringify({ ok: false, error: message }), {
              status: 400,
              headers: { "Content-Type": "application/json" },
            });
          }
        }

        return new Response("Not Found", { status: 404 });
      },
    });

    console.log(`[happy-solver] Daemon listening on http://127.0.0.1:${port}`);
    return;
  }

  // CLI Mode
  let scene = DEFAULT_SCENE;
  let region = DEFAULT_REGION;
  let prefix = DEFAULT_PREFIX;
  let proxy: string | undefined;

  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === "--scene" && argv[i + 1]) scene = argv[++i];
    else if (argv[i] === "--region" && argv[i + 1]) region = argv[++i];
    else if (argv[i] === "--prefix" && argv[i + 1]) prefix = argv[++i];
    else if (argv[i] === "--proxy" && argv[i + 1]) proxy = argv[++i];
  }

  const res = await solveCaptcha({ scene, region, prefix, proxy });
  console.log(JSON.stringify(res, null, 2));
  process.exit(res.ok ? 0 : 1);
}

if (import.meta.main) {
  main().catch((err) => {
    console.error(err);
    process.exit(1);
  });
}
