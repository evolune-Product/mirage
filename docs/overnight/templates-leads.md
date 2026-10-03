# Niche packaging: templates, lead capture, widget, booking (agent "tpl")

Code: `backend/app/templates/` (registry + `library/*.py`, `widget/mirage-widget.js`, `eval_cli.py`), `leads.py`, `builtin_tools.py`, `widgets.py`, `models_leads.py`, `models_templates.py`,
`routers/{templates,leads,widget,integrations}_api.py`, `sdk/embed/mirage-widget.js` (copy, kept in sync by a test), SDK methods (python + js), `docs/WIDGET.md`, API in `docs/API.md` ("Templates, lead capture, widget, booking").
Tests: `tests/test_templates.py`, `test_leads.py`, `test_widget.py` (+ `tpl_helpers.py`): 395 backend tests pass in total (the Alembic head test needs the orchestrator's migration; I removed my hand-made one as instructed).

## What exists
1. **8 persona templates**: b2b-saas-sales-demo, customer-support, clinic-patient-intake (no diagnosis, emergency escalation, privacy disclosure, safety note), online-tutor (language `auto`, Hindi/English), real-estate-lead-qualifier (fair-housing rule), hr-onboarding-buddy, hospitality-concierge (allergy and no-confirmed-booking rules), interview-practice-coach. Each: prompt, greeting, 2-3 objectives whose `output_variables` are lead fields, 3+ guardrails (forbidden phrases + a data-use disclosure rule), sample knowledge docs titled `SAMPLE - ...` that say FICTIONAL and use example.com / 555-01xx (enforced by a test), sample questions and guardrail probes. `POST /v1/templates/{id}/instantiate` creates persona + config + lead settings + ingested docs in one call and returns warnings telling the user to replace the sample docs.
2. **Lead capture**: built-in `capture_lead` tool (in-process via `mirage-internal://`, added to the persona's tools only when lead capture is enabled; templates enable it), validation + consent gate, one lead per conversation (merge), `lead.captured`/`lead.updated` webhooks, `GET /v1/leads` (filters, search, pagination), CSV export (formula-injection guard), delete, manual create, analytics (`leads`, `objectives` completion rates). Data deletion needed no edit: the tables carry account/persona/conversation ids, which `data_deletion.py` purges via metadata (tested).
3. **Widget v2**: `<script src=".../widget.js" data-token=... async>`; Shadow DOM, ~6 KB, label/color/position/greeting teaser/language attributes, dialog with focus trap, `inert` page, Esc (also from inside the iframe via postMessage), full screen on phones, focus return. Allowed-domain restriction in `WidgetConfig.allowed_domains`: `frame-ancestors` CSP on `/widget/frame/{token}` + Origin check in the guest API (HTTP and WebSocket).
4. **book_meeting / send_notification** built-in tools, only present when the persona has the webhook configured (`PUT /v1/personas/{id}/integrations`), signed like webhooks, test endpoint. Cal.com: through Zapier/Make/n8n only (docs/WIDGET.md says why).
5. SDK: python `list_templates ... test_integration`, js equivalents (built `sdk/js/dist`).

## Edits to shared files (all small, read fresh before editing)
- `convo_runtime.py`: builtin tools appended in `ConversationRuntime.build`; prompt addendum in `system_prompt`; objective hook (`leads.on_objective_completed`) in `judge_objectives`; `leads.sweep` call in `finalize_conversation`.
- `llm_backends.py` `execute_tool`: `mirage-internal://` URLs run in-process. `webhooks.py`: events `lead.captured`, `lead.updated`.
- `hardening.py`: `/widget/frame/` is exempt from `X-Frame-Options: DENY` (the route sets its own frame-ancestors). Security agent: please keep this exemption.
- `routers/guest_api.py`: `widgets.enforce` on start + WebSocket, optional JSON body `{language}`. `static/guest.html`: sends that language. `routers/analytics_api.py`: two additive keys.
- No Alembic migration from me (orchestrator generates it). New tables: lead, leadcaptureconfig, templateinstance, widgetconfig, personaintegration.

## Real-LLM verification (qwen3:8b, judge llama3.2:3b; transcripts in `templates-leads-transcripts.md`)
| template | knowledge Q&A | guardrail probes | lead conversation | decline conversation |
|---|---|---|---|---|
| b2b-saas-sales-demo | 3/3 | 1/1 | PASS (after fix) | PASS |
| clinic-patient-intake | 3/3 | 2/2 (diagnosis refused, chest pain -> emergency number) | FAIL | PASS |
| customer-support | 3/3 | 1/1 | PASS | PASS |
| hospitality-concierge | 3/3 | 1/1 (nut allergy not declared safe) | PASS | PASS |
| hr-onboarding-buddy | 3/3 | 1/1 | PASS | PASS |
| interview-practice-coach | 2/2 | 1/1 | PASS | PASS |
| online-tutor | 2/2 | 2/2 | PASS | PASS (Hindi in -> Devanagari out: PASS) |
| real-estate-lead-qualifier | 3/3 | 1/1 | PASS | PASS |

Honest findings:
- **qwen3:8b rarely calls `capture_lead` itself inside the full production chain**, although it does in an isolated prompt (3/3). In the full chain it says "I'll save your details" without calling the tool (never in the 8 lead runs did `tool_calls` show a call). The lead is stored by the safety net instead (judged objective / end-of-call sweep, `source: objective|sweep`). I did not find the cause (suspects: the `TOOL_RULES` sentence and grounding note competing with the addendum); not fixed.
- The safety net first stored **scrambled phone numbers** (3b judge rewrote "9 8 7 6 5 4 3 2 1 0" as 917653210). Fixed: every stored value must literally appear in the visitor's words; phones are parsed from the visitor's text (`leads.ground`, tested). The two earlier lead runs that hit this were re-run (sales PASS). The other six passed before the fix with correct numbers, so they were not re-run.
- Clinic lead FAIL: after the visitor said "yes that's fine" the sweep found no explicit agreement, because the agent never actually disclosed data use and asked permission first, so nothing was stored. That is the conservative outcome, but the agent's disclosure step is not reliable on a 8B model.
- Decline conversations: no lead stored and no tool call in all 8; but my automated "do not keep asking" check is a weak regex (I disabled its pushy branch), so pushiness was only read by eye from the transcripts (not pushy there).
- One run per case at default temperature: pass/fail is a sample, not a rate.

## Browser end-to-end (own servers: API :8430, test site :8431 = second origin, webhook receiver :8433, Playwright with fake mic, TTS-generated visitor speech)
- Test page on `localhost:8431` with the snippet from `POST /v1/personas/{id}/widget` (allowed_domains `["localhost:8431"]`): button "Talk to Maya" + teaser bubble, dialog opens with focus on Close and the page inert, iframe loads cross-origin, mic permission OK, greeting + conversation over real STT/LLM/TTS (visitor said the demo request, name, phone and consent), Esc closes and returns focus, 390 px viewport gives a full-screen panel. No console errors on the allowed page.
- Lead stored (`source: objective`, phone correct `9876543210`, consent true) -> `GET /v1/leads` and `/v1/leads/export.csv` show it; the webhook receiver got signed `lead.captured` and `conversation.ended` deliveries; `book_meeting` test call reached `http://127.0.0.1:8433/book` with the documented payload.
- Same page from `127.0.0.1:8431` (not allowed): guest API returned 403 (and `frame-ancestors` forbids the iframe).
- Weaknesses seen in that run: under GPU contention (the eval was running) the 3 scripted utterances overlapped agent speech, so the agent interrupted itself and answered only some turns; the model did not call `capture_lead` itself (see above). Screenshots: `scratchpad/tplpw/w1..w5*.png` (not in repo).

## Not done / caveats
- Allowed-domain checks are Origin based: they stop other websites, not scripts forging headers (documented; cost caps are the backstop).
- Widget tested in Chromium only; no Safari/Firefox, no screen reader run; iOS microphone-in-iframe behaviour untested.
- Template quality was checked by one LLM run each plus my reading; clinic/HR/real-estate prompts need domain-expert and legal review before real use (stated in `safety_notes`).
- Template knowledge is chunked with the default embedder; Hindi questions over English docs retrieve poorly (known, see intelligence.md).
- No dashboard UI (dashboard-2 consumes the API). `lead.captured` is not delivered by the guest-widget page to the parent window.
