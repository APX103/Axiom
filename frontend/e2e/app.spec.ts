import { test, expect } from "@playwright/test";
import { spawn, ChildProcess } from "child_process";
import { createInterface } from "readline";
import path from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

let backend: ChildProcess | null = null;
let frontend: ChildProcess | null = null;

async function waitForUrl(url: string, timeoutMs = 30000): Promise<void> {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      const res = await fetch(url);
      if (res.ok) return;
    } catch {
      // ignore
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(`Timed out waiting for ${url}`);
}

function logProcess(name: string, proc: ChildProcess) {
  if (proc.stdout) {
    createInterface(proc.stdout).on("line", (line) => console.log(`[${name}] ${line}`));
  }
  if (proc.stderr) {
    createInterface(proc.stderr).on("line", (line) => console.error(`[${name}] ${line}`));
  }
}

test.beforeAll(async () => {
  // 启动 Python 后端
  backend = spawn("uv", ["run", "axiom", "serve"], {
    cwd: path.resolve(__dirname, "../.."),
    stdio: "pipe",
  });
  logProcess("backend", backend);

  // 启动前端 dev server
  frontend = spawn("bun", ["run", "dev"], {
    cwd: path.resolve(__dirname, "../../frontend"),
    stdio: "pipe",
  });
  logProcess("frontend", frontend);

  await waitForUrl("http://localhost:8000/api/health");
  await waitForUrl("http://localhost:5173/");
});

test.afterAll(async () => {
  frontend?.kill("SIGTERM");
  backend?.kill("SIGTERM");
  await new Promise((r) => setTimeout(r, 1000));
  frontend?.kill("SIGKILL");
  backend?.kill("SIGKILL");
});

test.describe("Axiom E2E", () => {
  test("loads the workbench and shows backend online", async ({ page }) => {
    await page.goto("/");
    await expect(page).toHaveTitle("Axiom");
    await expect(page.getByText("后端在线")).toBeVisible();
    await expect(page.getByText("Axiom").first()).toBeVisible();
  });

  test("toggles light/dark theme", async ({ page }) => {
    await page.goto("/");
    const root = page.locator("html");

    // 默认系统主题;强制切到暗色
    await page.getByRole("button", { name: /切换到月之暗面|切换到月之亮面/ }).click();
    await expect(root).toHaveClass(/dark/);

    await page.getByRole("button", { name: /切换到月之暗面|切换到月之亮面/ }).click();
    await expect(root).not.toHaveClass(/dark/);
  });

  test("starts a session and receives assistant reply", async ({ page }) => {
    test.setTimeout(120000);
    await page.goto("/");

    const input = page.getByPlaceholder("输入任务或问题");
    await input.fill("你好");
    await input.press("Enter");

    // 等待用户气泡
    await expect(page.getByText("你好").first()).toBeVisible();

    // 等待 AI 回复出现 (Axiom 头像旁边的任意文字)
    await expect(page.locator("text=Step").first()).toBeVisible({ timeout: 60000 });

    // 侧边栏出现会话记录
    await expect(page.getByText(/会话\s+[a-f0-9]+/)).toBeVisible();

    // 截图留存
    await page.screenshot({ path: "e2e/screenshot-reply.png", fullPage: true });
  });

  test("workspace panel lists files after code generation", async ({ page }) => {
    test.setTimeout(120000);
    await page.goto("/");

    await page.getByPlaceholder("输入任务或问题").fill("用 python 计算 17*23");
    await page.getByPlaceholder("输入任务或问题").press("Enter");

    // 等待回复完成并出现 token 用量 (说明 complete 事件到了)
    await expect(page.locator("text=↑").first()).toBeVisible({ timeout: 60000 });

    // 切换到项目标签看文件树
    await page.getByRole("button", { name: "项目" }).click();
    await expect(page.getByRole("heading", { name: "工作区" })).toBeVisible();

    await page.screenshot({ path: "e2e/screenshot-workspace.png", fullPage: true });
  });
});
