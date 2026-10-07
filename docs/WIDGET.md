# VocalFace embed widget (v2)

A floating "Talk to us" button that opens your AI agent in a dialog on any website. One script tag, **no API key** on the page (it uses a guest share token).

## 1. Create it (once, server side, with your API key)

```
curl -X POST $API/v1/personas/$PERSONA_ID/widget -H "x-api-key: $KEY" -H "content-type: application/json" \
  -d '{"allowed_domains":["example.com","*.example.com"],"label":"Talk to us","color":"#6d5efc","position":"bottom-right",
       "greeting":"Questions? Ask our AI assistant","language":"en","max_seconds":300,"max_total_seconds":3600}'
```
The response contains a ready-made `snippet`. Python: `m.create_widget(pid, allowed_domains=[...])`; JS: `vocalface.createWidget(pid, {...})`.

## 2. Paste the snippet before `</body>`

```html
<script src="https://YOUR-API-HOST/widget.js" data-token="sh_xxxxxxxx" async></script>
```

Optional attributes: `data-label` (button text, default "Talk to us"), `data-color` (`#rrggbb`), `data-position` (`bottom-right` | `bottom-left`),
`data-greeting` (teaser bubble next to the button, shown once per tab session), `data-language` (conversation language code such as `en`, `hi`, `es`, `auto`),
`data-host` (override the API origin). JS API: `window.__vocalfaceWidget.open()` / `.close()`.

## Behaviour
- The button opens a dialog (full screen on phones, a 400 px panel on desktop) with an iframe of `/widget/frame/<token>`; the microphone permission is requested by that page.
- Accessibility: `role="dialog"`, `aria-modal`, labelled; focus moves into the dialog and returns to the button on close; Tab stays inside the dialog (the rest of the page is `inert` while it is open); Esc closes it (also when focus is inside the iframe); `prefers-reduced-motion` respected; text colour on the button is chosen for contrast.
- Closing the dialog unloads the iframe, which ends the conversation.
- Styles are isolated in a Shadow DOM, so your CSS cannot break it and it cannot break yours. Script size is about 6 KB, no dependencies.

## Allowed domains
`allowed_domains` limits which websites may embed the agent: `example.com` (any port), `*.example.com` (subdomains only, not the apex), `localhost:8000`, `https://shop.example.com`. Empty list = any site.
Enforced by `Content-Security-Policy: frame-ancestors` on the iframe page (browsers refuse to frame it elsewhere) and by the guest API (`403`, WebSocket close `4403`) for browser requests whose `Origin` is not allowed. This protects against other websites reusing your token; it cannot stop a script that forges headers, so the share token also carries cost caps (`max_seconds`, `max_total_seconds`, sessions per hour and per IP). Revoke any time with `DELETE /v1/widgets/{token}`. Change domains with `PUT /v1/widgets/{token}`.

## Lead capture and booking from the widget
Create the persona from a template (lead capture on), then the agent asks politely for contact details (after saying how they are used), stores them with consent (`GET /v1/leads`, `/v1/leads/export.csv`) and sends the `lead.captured` webhook.

### Booking tool (`book_meeting`) with Cal.com, Zapier, Make, n8n
Set `PUT /v1/personas/{id}/integrations {"booking_webhook_url": "...", "booking_secret": "optional"}`. VocalFace POSTs
`{"tool":"book_meeting","arguments":{"name","email","preferred_time","timezone","topic","duration_minutes"},"conversation_id","persona_id"}` (signed with `VocalFace-Signature`, same scheme as webhooks, when a secret is set) and treats any 2xx as success. The agent only says the team will confirm; it never claims a booking is made.
- **Zapier / Make / n8n**: create a "Catch Hook" trigger, paste its URL, then add the action you want (Google Calendar event, Cal.com booking, email, Slack). Use `POST /v1/personas/{id}/integrations/test` to send a sample call (`arguments.test = true`).
- **Cal.com**: Cal.com's bookings API needs an exact `start` time and event type, which a voice agent cannot guarantee, so connect it through Zapier/Make/n8n (map `preferred_time` to a start, or create a booking request for a human to confirm). Direct Cal.com integration is not built.
- **Email notify**: `notify_webhook_url` enables `send_notification {subject, message, urgency, contact}`; point it at a Zap that sends an email or Slack message. VocalFace has no SMTP.
Both tools are optional; nothing is called unless you configure the URLs.
