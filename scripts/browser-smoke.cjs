/* Run against local DEMO data only. Reads ignored credentials; never logs them. */
const { chromium } = require("playwright");
const fs = require("node:fs");
const assert = require("node:assert/strict");

(async () => {
  const base = process.env.SMOKE_BASE_URL || "http://127.0.0.1:8000";
  assert(
    ["127.0.0.1", "localhost"].includes(new URL(base).hostname),
    "Smoke tests mutate demo data; only localhost is allowed.",
  );
  const accounts = JSON.parse(
    fs.readFileSync("var/demo-credentials.json", "utf8"),
  );
  const browser = await chromium.launch({
    executablePath: process.env.CHROMIUM_PATH || "/usr/bin/chromium",
    headless: true,
    args: [
      "--no-sandbox",
      "--use-fake-device-for-media-stream",
      "--use-fake-ui-for-media-stream",
    ],
  });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
    permissions: ["microphone"],
  });
  const page = await context.newPage();
  page.setDefaultTimeout(15000);
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  fs.mkdirSync("var/screenshots", { recursive: true });
  async function login(role) {
    await context.clearCookies();
    await page.goto(base + "/login/");
    const account = accounts.find((user) => user.role === role);
    await page.locator("[name=identifier]").fill(account.username);
    await page.locator("[name=password]").fill(account.password);
    await page.locator("button[type=submit], form button").first().click();
    await page.waitForURL(base + "/");
  }
  async function saveSettled() {
    await page.waitForFunction(() =>
      document
        .getElementById("save-status")
        .textContent.includes("Сохранено на сервере"),
    );
  }
  async function finishSection(next) {
    await page.locator("#submit-section").click();
    await page.locator("#confirm-submit").click();
    if (next)
      await page.waitForFunction(
        (kind) =>
          JSON.parse(document.getElementById("exam-state")?.textContent || "{}")
            .kind === kind,
        next,
      );
    else await page.waitForURL(/\/results\//);
  }
  try {
    await login("student");
    await page.screenshot({
      path: "var/screenshots/dashboard-desktop.png",
      fullPage: true,
    });
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth > innerWidth,
      ),
      false,
      "Mobile dashboard overflows",
    );
    await page.screenshot({
      path: "var/screenshots/dashboard-mobile.png",
      fullPage: true,
    });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto(base + "/exams/");
    await page.locator(".exam-card a").first().click();
    await page.locator("[name=ready]").check();
    await page.getByRole("button", { name: /Начать \/ продолжить/ }).click();
    await page.waitForURL(/\/attempts\//);
    const attemptUrl = page.url();
    const attemptId = attemptUrl.split("/").filter(Boolean).at(-1);
    await page.locator("[name=l1]").fill("Riverside");
    await page.locator("[name=l2][value=Thursday]").check();
    await page.locator("[name=l3]").fill("six");
    await page.locator("[name=l4][value=Headphones]").check();
    await page.locator("[name=l4][value=Laptops]").check();
    await saveSettled();
    const deadline = await page
      .locator("#exam-state")
      .textContent()
      .then(JSON.parse)
      .then((s) => s.deadline);
    await page.reload();
    assert.equal(await page.locator("[name=l1]").inputValue(), "Riverside");
    assert.equal(
      JSON.parse(await page.locator("#exam-state").textContent()).deadline,
      deadline,
    );
    await page.locator("#play-listening").click();
    await page.waitForFunction(
      () => document.getElementById("listening-audio").currentTime > 0,
    );
    await page.screenshot({
      path: "var/screenshots/listening.png",
      fullPage: true,
    });
    await finishSection("reading");
    await page.locator("[name=r1][value=False]").check();
    await page.locator('[name=r2][value="Not Given"]').check();
    await saveSettled();
    await context.setOffline(true);
    await page.locator("[name=r3]").fill("social connection");
    await page.locator("[name=r4]").fill("rainwater tanks");
    await page.waitForTimeout(1100);
    assert(
      await page.evaluate(() =>
        Object.keys(localStorage).some((key) => key.startsWith("zg-draft-")),
      ),
    );
    await context.setOffline(false);
    await saveSettled();
    await page.reload();
    assert.equal(
      await page.locator("[name=r4]").inputValue(),
      "rainwater tanks",
    );
    await page.screenshot({
      path: "var/screenshots/reading.png",
      fullPage: true,
    });
    await finishSection("writing");
    await page
      .locator("[name=w1]")
      .fill(
        "The table compares three learning resources. Online courses became more popular while the use of printed books declined. This is a browser smoke test response.",
      );
    await page
      .locator("[name=w2]")
      .fill(
        "Independent learning offers flexibility. However, a classroom can provide valuable feedback and structure. This is an original browser smoke test essay.",
      );
    await saveSettled();
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth > innerWidth,
      ),
      false,
      "Mobile exam overflows",
    );
    await page.screenshot({
      path: "var/screenshots/writing-mobile.png",
      fullPage: true,
    });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await finishSection("speaking");
    for (const id of ["s1", "s2", "s3"]) {
      const box = page.locator(`[data-recording=${id}]`);
      await box.locator(".record-start").click();
      await box.locator(".record-stop").waitFor({ state: "visible" });
      await page.waitForTimeout(1500);
      await box.locator(".record-stop").click();
      await page.waitForFunction(
        (q) =>
          document
            .querySelector(`[data-recording=${q}] .record-status`)
            .textContent.includes("сохранена на сервере"),
        id,
      );
    }
    await page.screenshot({
      path: "var/screenshots/speaking.png",
      fullPage: true,
    });
    await finishSection(null);
    assert(await page.getByText("Спасибо за вашу работу").isVisible());
    await login("teacher");
    await page.goto(`${base}/results/${attemptId}/`);
    for (const section of ["Writing", "Speaking"]) {
      await page.getByRole("link", { name: `Проверить ${section}` }).click();
      const selects = page.locator(".grading-form select");
      for (let i = 0; i < (await selects.count()); i++)
        await selects.nth(i).selectOption("7");
      await page
        .locator("[name=feedback]")
        .fill(`Browser smoke: ${section} reviewed by assigned teacher.`);
      await page.getByRole("button", { name: /Завершить проверку/ }).click();
      await page.waitForLoadState("domcontentloaded");
      await page.getByRole("link", { name: /К результату/ }).click();
    }
    await login("super");
    await page.goto(`${base}/results/${attemptId}/`);
    await page.getByRole("button", { name: /Опубликовать результат/ }).click();
    await page.waitForLoadState("domcontentloaded");
    await login("student");
    await page.goto(`${base}/results/${attemptId}/`);
    assert(
      await page
        .getByText("Browser smoke: Writing reviewed by assigned teacher.", {
          exact: true,
        })
        .isVisible(),
    );
    assert.equal(
      (await page.locator(".overall strong").textContent()).trim(),
      "8,0",
    );
    await page.screenshot({
      path: "var/screenshots/result.png",
      fullPage: true,
    });
    assert.deepEqual(errors, []);
    console.log(
      JSON.stringify({
        status: "passed",
        scenarios: [
          "login",
          "desktop/mobile layout",
          "server autosave",
          "reload and timer persistence",
          "listening playback",
          "offline recovery",
          "writing",
          "microphone recording and upload in three parts",
          "teacher review",
          "admin publication",
          "student result visibility",
        ],
        attempt: attemptId,
        javascriptErrors: errors,
      }),
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
