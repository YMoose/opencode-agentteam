import assert from "node:assert/strict"
import { mkdtemp, rm } from "node:fs/promises"
import os from "node:os"
import path from "node:path"
import { createInterface } from "node:readline"
import { Readable } from "node:stream"
import { Database } from "bun:sqlite"

if (process.argv[2] === "claim") {
  const db = new Database(process.argv[3])
  db.exec("PRAGMA busy_timeout = 5000")
  process.stdout.write("ready\n")
  await Bun.stdin.text()
  const result = db
    .query(
      "UPDATE task SET revision = revision + 1, owner = ? WHERE id = 1 AND revision = 1 AND owner IS NULL",
    )
    .run(process.argv[4])
  process.stdout.write(JSON.stringify({ owner: process.argv[4], changes: result.changes }) + "\n")
  db.close()
} else {
  const directory = await mkdtemp(path.join(os.tmpdir(), "team-p0-sqlite-"))
  const database = path.join(directory, "team.db")
  const db = new Database(database, { create: true })
  const children: ReturnType<typeof Bun.spawn>[] = []
  try {
    db.exec("PRAGMA journal_mode = WAL; PRAGMA synchronous = FULL")
    db.exec(
      "CREATE TABLE task (id INTEGER PRIMARY KEY, revision INTEGER NOT NULL, owner TEXT); CREATE TABLE outbox (id TEXT PRIMARY KEY, task_revision INTEGER NOT NULL)",
    )
    db.exec("INSERT INTO task VALUES (1, 1, NULL)")
    const contenders = ["member-a", "member-b"].map((name) => {
      const child = Bun.spawn([process.execPath, import.meta.path, "claim", database, name], {
        stdin: "pipe",
        stdout: "pipe",
        stderr: "pipe",
      })
      children.push(child)
      const lines = createInterface({ input: Readable.fromWeb(child.stdout) })[Symbol.asyncIterator]()
      return { child, lines }
    })
    const ready = await Promise.all(contenders.map(({ lines }) => lines.next()))
    assert(ready.every((item) => item.value === "ready"))
    for (const { child } of contenders) child.stdin.end("go\n")
    const claims = await Promise.all(
      contenders.map(async ({ child, lines }) => {
        const result = await lines.next()
        const stderr = await new Response(child.stderr).text()
        assert.equal(await child.exited, 0, stderr)
        return JSON.parse(result.value)
      }),
    )
    assert.deepEqual(claims.map((item) => item.changes).sort(), [0, 1])
    const before = db.query("SELECT * FROM task").get()
    assert.equal(before.revision, 2)
    assert.throws(
      () =>
        db.transaction(() => {
          db.exec("UPDATE task SET revision = 3 WHERE id = 1")
          db.exec("INSERT INTO outbox VALUES ('notify-rollback', 3)")
          throw new Error("injected failure")
        })(),
      /injected failure/,
    )
    assert.deepEqual(db.query("SELECT * FROM task").get(), before)
    assert.equal(db.query("SELECT count(*) AS n FROM outbox").get().n, 0)
    db.transaction(() => {
      db.exec("UPDATE task SET revision = 3 WHERE id = 1")
      db.exec("INSERT INTO outbox VALUES ('notify-commit', 3)")
    })()
    db.close()
    const reopened = new Database(database)
    assert.equal(reopened.query("SELECT revision FROM task").get().revision, 3)
    assert.deepEqual(reopened.query("SELECT * FROM outbox").all(), [
      { id: "notify-commit", task_revision: 3 },
    ])
    reopened.close()
    console.log(
      JSON.stringify(
        {
          status: "passed",
          bun: Bun.version,
          checks: [
            "two processes competing at revision 1 yield exactly one successful claim",
            "injected transaction failure rolls back task and outbox together",
            "committed task and notification survive database reopen",
          ],
          claims,
        },
        null,
        2,
      ),
    )
  } finally {
    for (const child of children) {
      if (child.exitCode === null) child.kill()
      await child.exited
    }
    db.close()
    await rm(directory, { recursive: true, force: true })
  }
}
