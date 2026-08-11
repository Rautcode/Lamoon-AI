# Lamoon — Design System

The interaction model, visual language, and tokens behind the product. Every
token here is **implemented** in `apps/web/app/globals.css`; this document
explains the reasoning, not a parallel spec that can drift from it.

---

## 1. The governing rule

> **Lamoon is monochrome. The only chroma in the product belongs to Lumo.**

Human surfaces are warm neutrals. Anything iridescent (violet → cyan) is the
AI speaking. Semantic states are desaturated dots and text — never saturated
fills.

This is not a stylistic preference; it's the mechanism that makes the product
AI-first *visually* rather than just in marketing copy. Your eye goes to the
AI because it is the only colored thing on screen.

**It is enforceable in review:** a colored button that isn't Lumo is a bug.

Why warm neutrals rather than the usual blue-greys: blue-grey is the house
style of every enterprise tool this product is trying not to resemble. Warm
stone reads human and premium, and it makes the cool Lumo gradient sing
against it.

---

## 2. Interaction model — Ask → Act

Traditional HRMS: *navigate → find the screen → find the record → act.*
Lamoon: **ask → act.**

Three surfaces, in order of expected use:

| Surface | Invoke | Purpose |
|---|---|---|
| **Lumo** | `⌘J` or the orb | Ask questions in natural language, get real answers |
| **Command palette** | `⌘K` | Universal search + quick actions. The primary way to move |
| **Rail** | hover the left edge | Fallback navigation for people who'd rather point |

The rail is collapsed to 56px **by default and stays that way**, because the
palette makes it mostly redundant. A permanent 240px sidebar of links you
rarely click is exactly the habit being avoided. Everything is reachable in
two keystrokes.

### Lumo's grounding contract

Lumo runs Gemini with **tool calling** (`apps/api/app/modules/assistant/`).
The split that matters:

| | Source |
|---|---|
| **Prose** | The model |
| **Facts** | Tools only (`tools.py`) — real, RLS-scoped DB rows |
| **Clickable results** | Tools only — passed *around* the model, never through it |

So a hallucinated name can never become a link the user can click. If the
model invents something in its sentence, that's a visible prose error; it can
never invent an entity the product then treats as real. There's a test
asserting exactly this (`test_model_cannot_invent_clickable_results`).

**Two paths, one set of tools.** With `GEMINI_API_KEY` set, the model chooses
tools and writes the answer. Without it — or if the model call fails — a
keyword router picks one tool and uses its own sentence. The product answers
correctly either way; the model is an upgrade to the *language*, never to the
*facts*. The UI shows a small **`direct`** badge when the fallback answered,
so the interface never implies intelligence that isn't switched on.

Tools available: headcount, who's on leave (any date), pending approvals,
candidate search by tier with AI scores, open roles, departments, person
lookup. The loop is bounded to 3 steps so a confused model can't spin.

---

## 3. Information architecture

```
/login                     Workspace + email/password, or Google/Microsoft
/oauth/callback            Token handoff (fragment), never shown for long
│
└── (workspace)            Authenticated shell: rail + palette + Lumo
    /me                    Self-service — today's clock, leave balance, own
                             requests. The ONLY page an employee-role sees.
    /home                  AI Workspace — greeting, ask, signals
    /hiring                Pipeline board (Kanban) + candidate inspector
    /people                Directory (tiles, department filter)
    /people/[id]           One intelligent page per person
    /time                  Leave — decisions first, then history
    /attendance            Presence now + a 14-day hours heatmap
                             (weekends/holidays marked, not blank)
    /pay                   Payroll runs. HR/admin only — NOT managers
    /org                   Department tree + unassigned people
```

Nav is **derived from permissions**, not fixed (`lib/nav.ts`) — an employee
holds only `self.*` and sees exactly one destination, `/me`; HR sees six. One
source of truth feeds both the rail and the route guard, so a link can never
appear that the guard would bounce.

Every additional top-level item is a tax on the product's clarity; modules
that arrive later (performance, learning) should earn their slot or live
inside an existing one.

### Home is not a dashboard

No widget grid, no KPI tiles, no charts nobody asked for. `/home` is:

1. A time-aware greeting using the signed-in person's **real name** (this is
   why `/auth/me` returns `full_name` — a hardcoded "Sarah" would be a lie).
2. One large input.
3. **"Needs you"** — a short list of things genuinely awaiting a human, each
   a real count from live data, each a link to the place it's resolved.

If nothing needs you, the list says so and shuts up. A dashboard that always
shows twelve numbers trains you to ignore all twelve.

---

## 4. Core user flows

**Triage a candidate**
`⌘K → "backend" → Enter` *or* `/hiring` → board → click card → inspector
(score, AI summary, ask Lumo) → act.

**Approve leave**
`/home` → "3 leave requests waiting" → `/time` → decisions are the first
section → Approve/Decline inline. No drilling.

**Find a person**
`⌘K → type name → Enter` → profile. Two keystrokes from anywhere in the app.

**Ask a question**
`⌘J` → type → Lumo answers from live data, with clickable results that
navigate into the product.

---

## 5. Visual language

### Typography
Geist Sans throughout; one family, differentiated by size and tracking.

| Class | Use | Spec |
|---|---|---|
| `.t-display` | Page titles, greeting | `clamp(2rem, 1.3rem + 2.4vw, 3.25rem)` / 1.04 / `-0.035em` / 500 |
| `.t-title` | Section titles, inspector headers | 22px / 1.25 / `-0.02em` / 550 |
| `.t-body` | Prose | 15px / 1.6 / `-0.005em` |
| `.t-meta` | Secondary info | 13px / 1.5 / ink-3 |
| `.t-micro` | Section labels | 11px / `0.09em` / 600 / uppercase |

Negative tracking on display sizes is what separates "designed" from
"default". `.t-micro` is **the only place ALL CAPS is permitted**.

### Color

Four-step ink ramp (`--ink-1` … `--ink-4`) and four surface steps
(`--surface-0` … `--surface-3`). Four steps is enough; more invites
inconsistency without adding clarity.

Elevation comes from **background lift**, not borders. Hairlines
(`--hairline`, 8–9% ink) appear only where separation is genuinely load-
bearing. There is no `border: 1px solid grey` in this product by default.

### Space & radius
8px base. Section rhythm is generous: 48 / 56 / 96px between major blocks.
Radius: 8 / 12 / 16 / 20 / 28, plus full pills. Cards are 16px; floating
panels 28px.

---

## 6. Motion

**One easing curve for the entire product:** `cubic-bezier(0.32, 0.72, 0, 1)`
(`--ease-out-expo`). A single curve is what makes a UI feel like one object
rather than a pile of components.

| Token | Duration | Use |
|---|---|---|
| micro | 150ms | hover, press, color |
| `.pop` | 200ms | panels, palette, inspector entrance |
| `.fade` | 280ms | page content |
| `.rise` | 420ms | first-paint entrance |
| `.stagger` | +45ms/child | lists, boards, signal rows |

Named keyframes: `rise`, `fade`, `pop`, `breathe` (Lumo idle), `think` (Lumo
typing indicator).

`prefers-reduced-motion` collapses everything to 0.01ms. This is
non-negotiable and implemented globally, not per-component.

---

## 7. Component library

Implemented in `apps/web/components/lamoon/`:

| Component | Notes |
|---|---|
| `Surface` / `surface-raised` | Borderless containers; the only "card" |
| `SectionLabel` | Micro-label + optional action |
| `Status` | Desaturated dot + text. Never a filled badge |
| `Pill` | Neutral chip |
| `Avatar` | Deterministic initials — same person, same tile, always |
| `Action` | Button: `primary` (inverted ink), `quiet`, `ghost`. **Never colored** |
| `Metric` | Big number + micro-label. No box |
| `Kbd`, `Empty` | Keyboard hints, empty states |
| `LumoMark` | The iridescent mark, with a `thinking` state |
| `Rail`, `CommandPalette`, `Lumo` | The three shell surfaces |
| `ScoreDial` | Ring showing AI score /10 (hiring) |
| `BalanceRing` | Apple-Health-style leave ring (profile) |

Deliberately **no** traditional `Card`+`CardHeader`+`CardTitle` stack for
product surfaces — that pattern is what makes enterprise software look like a
pile of boxes. shadcn's `Input`, `Label`, and `Select` are retained because
they're solid and accessible; they inherit the tokens above rather than
fighting them.

---

## 8. Responsive

Mobile-first breakpoint at `sm` (640px).

| | Mobile (<640) | Tablet / Desktop (≥640) |
|---|---|---|
| Nav | Bottom bar, thumb-reachable, safe-area aware | Left rail, hover-expands |
| Lumo | Full-screen sheet | 420×620 floating panel |
| Inspector | Full-screen | 440px right drawer |
| Board | Horizontal scroll **inside** the board | Same — stages stay visible |
| Directory | 1 column | 2 (sm) → 3 (lg) |

The board keeps horizontal scroll on mobile rather than collapsing to a list,
because collapsing hides the stages — which are the entire point of a board.
The page itself never scrolls horizontally at any width (verified).

---

## 9. Accessibility

- Visible focus rings on `:focus-visible` (keyboard only), Lumo-colored, 2px + 2px offset.
- Full keyboard model: `⌘K`, `⌘J`, `Esc`, `↑`/`↓`/`Enter` in the palette.
- `aria-current="page"` on active nav; `aria-label` on every icon-only control.
- Status is **never** color-only — every dot is paired with text.
- `prefers-reduced-motion` fully respected.
- Theme set pre-paint to avoid a flash; `useSyncExternalStore` keeps toggle
  state honest rather than an effect that can desync.

---

## 10. Figma mapping

There is no `.fig` file here — I can't produce binary Figma documents. What
exists is a structure that maps 1:1 so rebuilding it in Figma is mechanical:

```
Tokens (Variables)
  surface/0..3 · ink/1..4 · lumo/base · lumo/alt · lumo/soft
  positive · caution · critical · hairline
  radius/sm..2xl · space/1..12 (8px base)

Text styles
  display · title · body · meta · micro     (§5, exact specs)

Components (variants)
  Action        variant=primary|quiet|ghost × size=sm|md × state
  Surface       elevation=flat|raised
  Status        tone=positive|caution|critical|neutral
  Avatar        size=22|28|36|42|76
  LumoMark      state=idle|thinking
  Pill · Kbd · SectionLabel · ScoreDial · BalanceRing

Screens        Login · Home · Hiring(+Inspector) · People · Person · Time · Org
               each in Light + Dark, at 375 / 768 / 1280
```

Token names match the CSS variables exactly, so a Figma variable and a CSS
custom property are never out of sync by name.

---

## 11. What is deliberately not built

Honesty about scope matters more than a complete-looking spec:

- **Income tax (TDS) computation.** Payroll computes PF, ESI and professional
  tax on a statutory wage derived per the Code on Wages definition (effective
  21 Nov 2025, versioned in `payroll/rules.py` and stamped into every
  payslip); it does NOT compute income tax. Doing that properly needs regime
  election, investment declarations and the proofs behind them — a subsystem
  that doesn't exist here. TDS is an input, entered from what the employer's
  accountant advises, and the UI says so on the screen where it's typed.
  Half-computing it would produce authoritative-looking numbers that are
  wrong, and the employer wears the penalty for short deduction.
- **Also not in payroll:** gratuity, statutory bonus, labour welfare fund,
  bank/NEFT export, arrears, reimbursements, half-days, and effective-dated
  salary revisions (a raise overwrites; history survives in frozen payslips).
- **Statutory filing.** No ECR, 24Q or ESI return, no challan or
  reconciliation. Payroll computes the liability; it does not discharge it.
  This is the gap between a payroll calculator and a payroll system.
- **Contractor workforce.** Work facts carry a site and a shift, but there is
  no contractor, deployment or invoice-reconciliation model.
- **F&F and gratuity**, and effective-dated salary structures.
- **Performance, LMS, assets, documents** — no API exists for
  these. The employee profile shows only real sections rather than
  greyed-out panels promising features that don't exist.
- **Shifts, overtime rules, geofenced or biometric punching.** The ledger
  supports them (a punch is just a timestamped event), but the policy and
  hardware decisions behind them aren't made.
- **Drag-to-move on the hiring board** — the API has `screen`/`advance`/
  `reject`, not a general "set stage". A board that silently fails to persist
  a drag is worse than one that doesn't offer it.
- **Analytics / charts** — deferred rather than faked with sample data.
- **Half-days and leave beyond whole working days.** Leave is billed in whole
  working days against the work week and holiday calendar. A half-day, or a
  holiday that applies to one location but not another, isn't modelled.
- **Lumo write actions** — it reads, it doesn't act. "Approve Asha's leave"
  is not wired, and shouldn't be until there's a confirmation step: a model
  that can mutate HR records on a misparse is a different risk class than one
  that answers a question wrongly.
- **Multi-turn memory** — each question is independent; Lumo doesn't
  remember the previous one.
