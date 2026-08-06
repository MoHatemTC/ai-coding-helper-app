import { test, expect } from "@playwright/test";

/**
 * Frontend smoke tests.
 *
 * Two groups:
 *  - "no backend required" — exercise pages, redirects, client-side
 *    validation, and the file-attach UI in isolation. These pass with only
 *    `npm run test:smoke` and nothing else running.
 *  - "requires live backend" — exercise a real register/login round trip
 *    and a real chat send. These need the FastAPI backend up at
 *    NEXT_PUBLIC_API_URL (see frontend/.env.local) and are skipped
 *    automatically if that backend isn't reachable, so the suite as a
 *    whole still passes in an environment with no backend.
 */

const FAKE_SESSION = {
  ach_user_token: "smoke-test-user-token",
  ach_session_token: "smoke-test-session-token",
  ach_session_id: "smoke-test-session-id",
  ach_session_name: "Smoke test session",
};

async function seedFakeSession(page: import("@playwright/test").Page) {
  await page.addInitScript((session) => {
    for (const [key, value] of Object.entries(session)) {
      window.localStorage.setItem(key, value as string);
    }
  }, FAKE_SESSION);
}

test.describe("no backend required", () => {
  test("root redirects unauthenticated users to /login", async ({ page }) => {
    await page.goto("/");
    await expect(page).toHaveURL(/\/login$/);
    await expect(page.getByRole("heading", { name: "Coding Helper" })).toBeVisible();
  });

  test("chat redirects unauthenticated users to /login", async ({ page }) => {
    await page.goto("/chat");
    await expect(page).toHaveURL(/\/login$/);
  });

  test("login page renders and toggles to register mode", async ({ page }) => {
    await page.goto("/login");
    await expect(page.getByPlaceholder("you@example.com")).toBeVisible();
    await expect(page.getByPlaceholder("••••••••")).toBeVisible();
    await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();

    await page.getByRole("button", { name: "Need an account? Register" }).click();
    await expect(page.getByRole("button", { name: "Create account" })).toBeVisible();
    await expect(page.getByPlaceholder("Jana")).toBeVisible();
  });

  test("login form blocks submit with empty required fields", async ({ page }) => {
    await page.goto("/login");
    await page.getByRole("button", { name: "Sign in" }).click();
    // HTML5 required-field validation keeps us on the same page rather than
    // firing a network request with empty credentials.
    await expect(page).toHaveURL(/\/login$/);
  });

  test("unmatched route shows the styled not-found page, not a broken screen", async ({ page }) => {
    await page.goto("/this-route-does-not-exist");
    await expect(page.getByRole("heading", { name: "Page not found" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Go home" })).toBeVisible();
  });

  test("authenticated chat page renders without crashing when the backend is unreachable", async ({ page }) => {
    await seedFakeSession(page);
    await page.goto("/chat");
    await expect(page).toHaveURL(/\/chat$/);
    // With a fake token and no real backend, the history fetch fails — the
    // page should show a friendly, non-technical error, never a blank
    // screen or raw exception text.
    await expect(page.getByText(/couldn't|failed|went wrong/i)).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText(/traceback|exception|stack/i)).toHaveCount(0);
  });

  test("attach-code panel accepts pasted code and a dropped/uploaded file", async ({ page }) => {
    await seedFakeSession(page);
    await page.goto("/chat");

    await page.getByRole("button", { name: "+ Attach code" }).click();
    const textarea = page.getByPlaceholder(/Paste the code you want reviewed/i);
    await textarea.fill("console.log('smoke test');");
    await expect(textarea).toHaveValue("console.log('smoke test');");

    // Target the (hidden) file input directly rather than going through the
    // native OS file-picker dialog — setInputFiles works on hidden inputs
    // and is the standard, non-flaky way to drive a file upload in Playwright.
    await page.locator('input[type="file"]').setInputFiles({
      name: "smoke.py",
      mimeType: "text/x-python",
      buffer: Buffer.from("print('hello from smoke test')\n"),
    });

    await expect(page.getByText("smoke.py")).toBeVisible();
  });

  test("oversized file attachment is rejected with a friendly message, not a crash", async ({ page }) => {
    await seedFakeSession(page);
    await page.goto("/chat");
    await page.getByRole("button", { name: "+ Attach code" }).click();

    await page.locator('input[type="file"]').setInputFiles({
      name: "too-big.py",
      mimeType: "text/x-python",
      buffer: Buffer.alloc(25_000, "a"), // over the 20,000-byte MAX_UPLOAD_BYTES cap
    });

    await expect(page.getByText(/too large/i)).toBeVisible();
  });
});

test.describe("requires live backend", () => {
  test.beforeEach(async () => {
    const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
    try {
      const res = await fetch(`${apiUrl}/health`, { signal: AbortSignal.timeout(2000) });
      test.skip(!res.ok, `Backend at ${apiUrl} is not healthy — skipping live-backend smoke tests.`);
    } catch {
      test.skip(true, `Backend at ${apiUrl} is not reachable — skipping live-backend smoke tests.`);
    }
  });

  test("register -> land in chat -> log out round trip", async ({ page }) => {
    const email = `smoke-${Date.now()}@example.com`;
    await page.goto("/login");
    await page.getByRole("button", { name: "Need an account? Register" }).click();
    await page.getByPlaceholder("you@example.com").fill(email);
    await page.getByPlaceholder("••••••••").fill("Smoke-Test-Pass1!");
    await page.getByRole("button", { name: "Create account" }).click();

    await expect(page).toHaveURL(/\/chat$/, { timeout: 15_000 });
    await page.getByRole("button", { name: "Log out" }).click();
    await expect(page).toHaveURL(/\/login$/);
  });
});
