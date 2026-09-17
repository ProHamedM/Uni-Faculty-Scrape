## What and why

## How I tested
- [ ] `pytest` passes (offline)
- [ ] `ruff check src tests` is clean
- [ ] Dry run: `unifaculty run -u <slug> --llm mock --max-pages 30 -v`
- [ ] Real run on: <university> (summary totals: kept / rejected / pages)

## Checklist
- [ ] No weakening of guardrails (robots.txt, delays, stop-on-block, scope, verification)
- [ ] New behavior is logged with structured events / spans
- [ ] No secrets, CSVs, run folders or personal data committed
- [ ] Docs updated (README / CONTRIBUTING / docs/) if user-facing
