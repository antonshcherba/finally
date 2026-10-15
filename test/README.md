# E2E tests
Docker: `docker compose -f docker-compose.test.yml up --build --abort-on-container-exit`
Local: start app on :8000 with LLM_MOCK=true, then `npm install && npx playwright install chromium && npx playwright test`.
