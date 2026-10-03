# dashboard-2 (agent ui2): every shipped backend feature in the UI

Ports used: api 8420, web 3420 (NEXT_DIST=.next-ui2), video 8822. No commits made.

## What was built
- **Replicas**: New replica modal with video/photo tabs (`POST /v1/replicas/photo`, closed-mouth explainer, URL preview, idle length + head motion, server rejection shown inline). Photo replicas: source photo as thumbnail, "Animating" stepper, photo progress/error/warnings, idle-loop preview. Details drawer auto-refreshes (5 s) and gained Cloned voice panel (status, similarity with plain-language verdict, WER/SNR/speed, preview playback, re-clone, delete, consent-requirement messaging), Background section (colour/gradient/image upload or URL/blur with live preview, save/remove), photo section. Listening clip stays URL-only (API has no upload).
- **Videos**: collapsible Studio options (aspect 16:9/9:16/1:1, resolution, caption style + accent, background, logo upload/URL with position/size/opacity, voice incl. replica's cloned voice, transitions, thumbnail, mouth sharpening) with a Look preview; script editor with Text/Scenes views (add/delete/reorder scenes, server split count + estimate, 12-scene limit message); studio jobs go to `/video-jobs/render` (options also sent to bulk/translate); video cards show stage + per-scene progress, thumbnail poster, MP4/thumbnail/SRT links via signed URLs, error text + attempts from `/jobs/video/{id}`.
- **Personas**: Perception tab (consent_acknowledged checkbox with plain privacy note, opt-in, camera/screen, VLM from `/perception/models`, interval, store frames), cloned voices in the voice picker with latency warning, model recommendation chips + eval numbers, Embed & leads tab (widget generator + snippet, lead capture settings, booking/notify integrations with test call), From template button.
- **Account**: Team page (workspaces list/create, members, role change, invite code shown once, pending invites, join by code, delete, switch via `X-Workspace` in lib/api.ts, sidebar chip); Billing gained allowance tiles, overage toggle + spend cap, usage report (month picker, daily chart, by kind, overage), owner-only message on 403.
- **Conversations**: iframe allow list now includes display-capture, headphones tip, perception indicator for the selected persona.
- **Onboarding** `/dashboard/onboarding`: 5 steps (template, replica photo/video, consent recorder, persona from template, live test), progress bar, per-step skip, resumable (localStorage); Overview shows a banner for accounts with no replicas/personas.
- **Templates / Leads**: TemplatePicker (niche filter, variables, sample-knowledge + lead toggles, warnings/next steps, static fallback if `/v1/templates` is absent); Leads page (search, persona/date/consent filters, pagination, detail drawer, delete, CSV export via authenticated fetch).

## Verification
- `npx tsc --noEmit` clean; `make e2e-smoke` green (9 passed, 4 skipped); new routes are auto-discovered by e2e/lib/routes.mjs.
- Screenshots looked at at 1440 and 390 for replicas, drawer, new-replica modal, videos (studio), billing, team, leads, embed tab, onboarding, plus 390 for all main pages: no horizontal overflow, no page errors.
- Real-browser functional script (scratchpad ui2_func.mjs), all pass: real cloned-voice preview audio, background save, studio video queued with 9:16/bold stored, template persona created, lead search, CSV download, overage saved, onboarding replica step.
- Bug found by the browser run and fixed: template `sample_questions` are objects `{q, expect}` (React crash).

## Honest caveats
- Expected 404s in the replica drawer console (no listening clip, no background set, typed consent has no audio/verification).
- NOT exercised: worker actually rendering a studio video (queued only), photo animation/rejection from the worker (the worker was not run on a photo), live perception in the playground, joining a workspace via the UI (API path tested only to the point of an email mismatch in my seed), workspace-mode conversations (the playground authenticates by the caller key, so a member may not connect while an X-Workspace is active; the Team page says so).
- Members cannot see "leave workspace": the API gives no own-account id for pure members.
- The Look preview for uploaded background images shows a placeholder (API serves no asset previews; the picker's own preview does show the just-uploaded file).
- Billing overage and report charts were checked with seeded ledger rows in my private DB.
- Photo upload is URL-only because `POST /replicas/photo` takes a URL.
