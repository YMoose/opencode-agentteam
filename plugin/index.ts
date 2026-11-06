import { Model, Plugin, Provider } from "@opencode/plugin"

// Isolated P0 fixture. Exposes test controls, not a production Team API.
export default Plugin.define({
  id: "research.dynamic-team.p0",
  async setup(ctx) {
    const requests: unknown[] = []
    const events: unknown[] = []
    const calls: unknown[] = []
    const barriers = new Map<string, () => void>()
    const instance = crypto.randomUUID()
    const usage = {
      inputTokens: { total: 1, noCache: 1, cacheRead: 0, cacheWrite: 0 },
      outputTokens: { total: 1, text: 1, reasoning: 0 },
    }
    const language = {
      specificationVersion: "v3" as const,
      provider: "team-p0",
      modelId: "fixture",
      supportedUrls: {},
      async doGenerate() { throw new Error("Unexpected non-streaming request") },
      async doStream(options: { prompt: unknown; abortSignal?: AbortSignal }) {
        const prompt = JSON.stringify(options.prompt)
        const barrier = prompt.match(/P0_BARRIER_([AB])/)?.[1]
        if (barrier) {
          await new Promise<void>((resolve, reject) => {
            barriers.set(barrier, resolve)
            options.abortSignal?.addEventListener("abort", () => reject(new Error("aborted")), { once: true })
          })
        }
        const target = prompt.match(/P0_TOOL_SEND:(ses_[a-zA-Z0-9_-]+)/)?.[1]
        const needsTool = target && !prompt.includes('"role":"tool"')
        return {
          stream: new ReadableStream({
            start(controller) {
              if (needsTool) {
                controller.enqueue({ type: "tool-call", toolCallId: "call-p0-send", toolName: "p0_send", input: JSON.stringify({ target }) })
              } else {
                controller.enqueue({ type: "text-start", id: "answer" })
                controller.enqueue({ type: "text-delta", id: "answer", delta: "P0 fixture response" })
                controller.enqueue({ type: "text-end", id: "answer" })
              }
              controller.enqueue({ type: "finish", finishReason: { unified: needsTool ? "tool-calls" : "stop", raw: needsTool ? "tool_calls" : "stop" }, usage })
              controller.close()
            },
          }),
        }
      },
    }
    await ctx.aisdk.hook("sdk", (event) => {
      if (event.model.providerID === "team-p0") event.sdk = { languageModel: () => language }
    })
    await ctx.aisdk.hook("language", (event) => {
      if (event.model.providerID === "team-p0") event.language = language
    })
    await ctx.provider.transform((editor) => editor.add({
      info: { ...Provider.Info.empty(Provider.ID.make("team-p0")), name: "P0 fixture", activation: "enabled", package: "aisdk:p0-fixture" },
      models: [{ ...Model.Info.default(Provider.ID.make("team-p0"), Model.ID.make("fixture")), capabilities: { tools: true, input: ["text"], output: ["text"] } }],
    }))
    await ctx.agent.transform((editor) => editor.update("p0-worker", (agent) => {
      agent.model = { providerID: "team-p0", id: "fixture" }
      agent.permissions = [{ action: "*", resource: "*", effect: "allow" }]
    }))
    await ctx.session.hook("context", (event) => {
      requests.push({ sessionID: event.sessionID, system: event.system, messages: event.messages })
    })
    await ctx.tool.transform((editor) => editor.add({
      name: "p0_send",
      description: "Send one fixture message and capture authoritative tool identity",
      input: { type: "object", properties: { target: { type: "string" } }, required: ["target"], additionalProperties: false },
      options: { codemode: false },
      async execute(input: { target: string }, context) {
        const origin = { sessionID: context.sessionID, messageID: context.messageID, callId: context.id }
        calls.push(origin)
        const admitted = await ctx.session.synthetic({
          sessionID: input.target,
          id: "msg_p0_tool_send",
          text: "P0 peer message",
          metadata: origin,
          resume: false,
          delivery: "queue",
        })
        return { content: JSON.stringify({ origin, admitted }) }
      },
    }))
    const iterator = ctx.event.subscribe({ types: [
      "session.inbox.enqueued", "session.inbox.delivered", "session.inbox.cancelled",
      "session.execution.started", "session.execution.succeeded", "session.execution.failed", "session.execution.interrupted",
    ] })[Symbol.asyncIterator]()
    const consume = async () => {
      for (;;) {
        const next = await iterator.next()
        if (next.done) return
        events.push(next.value)
      }
    }
    void consume().catch(() => {})
    await ctx.rpc.register({
      id: "team-p0",
      methods: { probe: { input: { type: "object" }, output: {} } },
      events: {},
    }, {
      async probe(input: { op: string; args?: any }) {
        const args = input.args ?? {}
        switch (input.op) {
          case "inspect": return { instance, location: ctx.location, requests, events, calls, barriers: [...barriers.keys()], sessionMethods: Object.keys(ctx.session) }
          case "release": for (const resolve of barriers.values()) resolve(); barriers.clear(); return { released: true }
          case "create": return ctx.session.create(args)
          case "get": return ctx.session.get(args)
          case "put": await ctx.session.instructions.entry.put(args); return { ok: true }
          case "entries": return ctx.session.instructions.entry.list(args)
          case "prompt": return ctx.session.prompt(args)
          case "synthetic": return ctx.session.synthetic(args)
          case "wait": return ctx.session.wait(args)
          case "compact": return ctx.session.compact(args)
          case "context": return ctx.session.context(args)
          case "permission":
            try { await ctx.permission.assert(args); return { allowed: true } }
            catch (error) { return { allowed: false, error: String(error) } }
          case "set": await ctx.storage.set(args.key, args.value); return { ok: true }
          case "getStorage": return { value: await ctx.storage.get(args.key) }
          case "race": {
            await ctx.storage.set("p0-race", 0)
            const before = await Promise.all([ctx.storage.get("p0-race"), ctx.storage.get("p0-race")])
            await Promise.all(before.map((value) => ctx.storage.set("p0-race", Number(value) + 1)))
            return { before, after: await ctx.storage.get("p0-race") }
          }
          default: throw new Error(`Unknown probe: ${input.op}`)
        }
      },
    })
    return () => { void iterator.return?.().catch(() => {}) }
  },
})
