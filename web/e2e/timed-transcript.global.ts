import { execFileSync } from "node:child_process";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";

const repositoryRoot = resolve(import.meta.dirname, "../..");
const projectPrefix = "stt-vault-timed-transcript-";
const externalBaseUrl =
  process.env.E2E_TIMED_TRANSCRIPT_BASE_URL ?? "http://127.0.0.1:18080";
const composeWaitTimeoutSeconds = 90;
const publicReadinessTimeoutMilliseconds = 30_000;
const composeFiles = [
  "-f",
  "docker-compose.yml",
  "-f",
  "docker-compose.e2e-timed-transcript.yml",
];

type E2ERun = {
  dataDirectory: string;
  fixtureDirectory: string;
  logPath: string;
  projectName: string;
};

export default async function globalSetup() {
  assertDockerComposeAvailable();
  const run = await createRun();
  const fixturePath = join(run.fixtureDirectory, "timed-transcript.wav");

  try {
    compose(run, [
      "up",
      "--build",
      "--wait",
      "--wait-timeout",
      String(composeWaitTimeoutSeconds),
    ]);
    await waitForPublicAppReadiness(run);
    compose(run, [
      "cp",
      "mod-whisper-cpu:/app/timed-transcript.wav",
      fixturePath,
    ]);
  } catch (error) {
    await cleanup(run);
    throw error;
  }

  process.env.E2E_TIMED_TRANSCRIPT_AUDIO_PATH = fixturePath;
  return async () => cleanup(run);
}

async function createRun(): Promise<E2ERun> {
  const fixtureDirectory = await mkdtemp(join(tmpdir(), projectPrefix));
  const dataDirectory = join(fixtureDirectory, "data");
  const projectName =
    process.env.E2E_TIMED_TRANSCRIPT_PROJECT ??
    `${projectPrefix}${process.pid}-${Date.now()}`;
  assertManagedProject(projectName);
  await mkdir(dataDirectory);

  const logPath = join(
    repositoryRoot,
    "web",
    "test-results",
    "timed-transcript-compose.log",
  );
  await mkdir(dirname(logPath), { recursive: true });
  return { dataDirectory, fixtureDirectory, logPath, projectName };
}

function assertDockerComposeAvailable() {
  try {
    execFileSync("docker", ["compose", "version"], {
      cwd: repositoryRoot,
      stdio: "pipe",
    });
  } catch (error) {
    throw new Error(
      "Timed transcript E2E requires Docker Compose. Install Docker with the Compose plugin and rerun pnpm --dir web e2e:timed-transcript.",
      { cause: error },
    );
  }
}

function assertManagedProject(projectName: string) {
  if (!new RegExp(`^${projectPrefix}[a-z0-9_-]+$`).test(projectName)) {
    throw new Error(
      `Refusing to manage non-E2E Compose project: ${projectName}`,
    );
  }
}

function assertManagedTemporaryDirectory(directory: string) {
  const expectedPrefix = join(tmpdir(), projectPrefix);
  if (!resolve(directory).startsWith(expectedPrefix)) {
    throw new Error(
      `Refusing to remove non-E2E temporary directory: ${directory}`,
    );
  }
}

function compose(run: E2ERun, arguments_: string[]) {
  assertManagedProject(run.projectName);
  const captureOutput = arguments_[0] === "logs" || arguments_[0] === "ps";
  const output = execFileSync(
    "docker",
    ["compose", "-p", run.projectName, ...composeFiles, ...arguments_],
    {
      cwd: repositoryRoot,
      env: {
        ...process.env,
        APP_PORT: "18080",
        STT_HOST_DATA_DIR: run.dataDirectory,
      },
      stdio: captureOutput ? "pipe" : "inherit",
      encoding: captureOutput ? "utf8" : undefined,
    },
  );
  return typeof output === "string" ? output : (output?.toString() ?? "");
}

async function waitForPublicAppReadiness(run: E2ERun) {
  const deadline = Date.now() + publicReadinessTimeoutMilliseconds;
  let lastFailure = "no readiness request completed";
  while (Date.now() < deadline) {
    try {
      const health = await fetch(`${externalBaseUrl}/api/health`, {
        signal: AbortSignal.timeout(3_000),
      });
      if (!health.ok) {
        lastFailure = `health returned ${health.status}`;
      } else {
        const auth = await fetch(`${externalBaseUrl}/api/auth/token`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ password: "e2e-admin-password" }),
          signal: AbortSignal.timeout(3_000),
        });
        if (auth.ok) return;
        lastFailure = `auth returned ${auth.status}`;
      }
    } catch (error) {
      lastFailure = error instanceof Error ? error.message : String(error);
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }

  throw new Error(
    `Timed transcript app was not reachable at ${externalBaseUrl}: ${lastFailure}\n${compose(
      run,
      ["ps", "--format", "json"],
    )}`,
  );
}

async function cleanup(run: E2ERun) {
  let cleanupError: unknown;
  try {
    const status = captureComposeDiagnostic(run, ["ps", "--format", "json"]);
    const logs = captureComposeDiagnostic(run, ["logs", "--no-color"]);
    await writeFile(
      run.logPath,
      `Compose status:\n${status}\nCompose logs:\n${logs}`,
      "utf8",
    );
    assertNoErrorDiagnostics(logs);
  } catch (error) {
    cleanupError = error;
  } finally {
    try {
      compose(run, ["down", "--volumes", "--remove-orphans"]);
    } finally {
      assertManagedTemporaryDirectory(run.fixtureDirectory);
      await rm(run.fixtureDirectory, { recursive: true, force: true });
    }
  }
  if (cleanupError) throw cleanupError;
}

function captureComposeDiagnostic(run: E2ERun, arguments_: string[]) {
  try {
    return compose(run, arguments_);
  } catch (error) {
    return error instanceof Error
      ? (error.stack ?? error.message)
      : String(error);
  }
}

function assertNoErrorDiagnostics(logs: string) {
  for (const line of logs.split("\n")) {
    if (!line.includes('"level"')) continue;
    const payload = line.slice(line.indexOf("{")).trim();
    try {
      const event = JSON.parse(payload) as { level?: string };
      if (event.level === "ERROR" || event.level === "WARNING") {
        throw new Error(`Compose reported ${event.level} diagnostics: ${line}`);
      }
    } catch (error) {
      if (error instanceof SyntaxError) continue;
      throw error;
    }
  }
}
