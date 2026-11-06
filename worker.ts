import { createInterface } from "node:readline"
import path from "node:path"
import { Effect } from "effect"
import { FetchHttpClient } from "effect/http"
import { Global } from "@opencode/util/global"
import { ensurePluginRuntime } from "@opencode/plugin/runtime"
import { ServerFetch } from "../src/fetch"

// Copied into packages/server/p0 by run.py; only the fixture imports public plugin APIs.
ensurePluginRuntime()
const root = process.argv[2]
const plugin = process.argv[3]
const globals = Object.fromEntries(
  ["home", "data", "cache", "config", "state", "tmp", "bin", "log", "repos"].map((key) => [
    key,
    path.join(root, key),
  ]),
)
const blocked: string[] = []
const noNetwork = Object.assign(
  async (input: Request | URL | string) => {
    const url = input instanceof Request ? input.url : String(input)
    blocked.push(url)
    throw new Error(`P0 forbids external HTTP: ${url}`)
  },
  { preconnect: fetch.preconnect },
)

await Effect.runPromise(
  Effect.gen(function* () {
    const handler = yield* ServerFetch.make(
      {
        app: { version: "team-p0" },
        database: { path: path.join(root, "p0.db") },
        config: {
          directory: globals.config,
          project: false,
          claude: false,
          content: JSON.stringify({
            plugins: [{ package: plugin, options: { database: path.join(root, "team-plugin.db") } }],
          }),
        },
        models: { fetch: false },
        events: { persist: true },
        fs: { filewatcher: false, fff: false },
      },
      { overrides: [Global.node.replace(Global.layerWith(globals))] },
    )
    process.stdout.write(JSON.stringify({ ready: true }) + "\n")
    yield* Effect.promise(async () => {
      for await (const line of createInterface({ input: process.stdin })) {
        const input = JSON.parse(line)
        try {
          if (input.stop) break
          const response = await handler(
            new Request(`http://opencode.local${input.path}`, {
              method: input.method ?? "GET",
              headers: { "content-type": "application/json" },
              ...(input.body === undefined ? {} : { body: JSON.stringify(input.body) }),
            }),
          )
          const body = await response.text()
          const parsed = response.headers.get("content-type")?.includes("text/event-stream")
            ? body
            : body
              ? JSON.parse(body)
              : null
          process.stdout.write(
            JSON.stringify({ id: input.id, status: response.status, body: parsed, blocked }) + "\n",
          )
        } catch (error) {
          process.stdout.write(JSON.stringify({ id: input.id, error: String(error), blocked }) + "\n")
        }
      }
    })
  }).pipe(Effect.scoped, Effect.provideService(FetchHttpClient.Fetch, noNetwork)),
)
