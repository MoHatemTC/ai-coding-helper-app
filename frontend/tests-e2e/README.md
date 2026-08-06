# Frontend smoke tests

```
npm install
npx playwright install chromium   # one-time, downloads a browser (needs network)
npm run test:smoke
```

8 of the 9 tests need nothing but the frontend itself (`playwright.config.ts` boots `npm run dev` automatically). The 9th (`requires live backend › register -> land in chat -> log out round trip`) pings `NEXT_PUBLIC_API_URL/health` first and skips itself automatically if the backend isn't up — start the backend first if you want that one to run too:

```
POSTGRES_HOST=localhost uv run uvicorn app.main:app --reload --port 8000
```

Last verified run (in the cloud sandbox, backend intentionally not running): **8 passed, 1 skipped**. See `smoke_test_run.txt` alongside this PR for the full output.
